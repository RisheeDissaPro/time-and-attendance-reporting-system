"""Run the attendance ETL: extract, stage, clean, load, then flag exceptions.

Usage::

    python -m etl.run_etl [--csv PATH] [--dry-run]

The whole run happens in one database transaction: either every step is
committed or nothing is. ``--dry-run`` performs every step and then rolls
back, which is useful for checking a new export.

Exit codes: 0 success, 1 run failed, 2 invalid configuration.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy.exc import SQLAlchemyError

from etl.config import ConfigError, Settings, load_settings
from etl.db import create_db_engine
from etl.extract import ExtractError, file_checksum, read_attendance_csv, stage_raw_attendance
from etl.load import fetch_employee_ids, fetch_rosters, insert_exceptions, upsert_attendance
from etl.logging_config import configure_logging
from etl.rules import apply_exception_rules
from etl.transform import clean_attendance

logger = logging.getLogger("etl.run_etl")


@dataclass
class EtlSummary:
    """Counts reported at the end of a run."""

    raw_rows: int = 0
    records_loaded: int = 0
    rows_rejected: int = 0
    duplicates_removed: int = 0
    exceptions_matched: int = 0
    exceptions_inserted: int = 0
    rejected_by_reason: dict[str, int] = field(default_factory=dict)


def run(settings: Settings, csv_path: Path, dry_run: bool = False) -> EtlSummary:
    """Execute one ETL run.

    Args:
        settings: Validated settings (database URL and rule thresholds).
        csv_path: Attendance export to process.
        dry_run: Roll back instead of committing.

    Returns:
        A summary of row counts.

    Raises:
        ExtractError: If the export is missing or malformed.
        SQLAlchemyError: On any database failure (the transaction is rolled back).
    """
    summary = EtlSummary()
    raw = read_attendance_csv(csv_path)
    summary.raw_rows = len(raw)
    checksum = file_checksum(csv_path)

    engine = create_db_engine(settings)
    with engine.connect() as conn:
        transaction = conn.begin()
        try:
            stage_raw_attendance(conn, raw, csv_path.name, checksum)

            cleaned = clean_attendance(raw, fetch_employee_ids(conn))
            summary.rows_rejected = len(cleaned.rejected)
            summary.rejected_by_reason = cleaned.rejected_by_reason
            summary.duplicates_removed = (cleaned.exact_duplicates_removed
                                          + cleaned.key_duplicates_removed)
            if cleaned.records.empty:
                logger.warning("No valid attendance records in %s", csv_path)
            else:
                stored = upsert_attendance(conn, cleaned.records)
                summary.records_loaded = len(stored)

                roster_frame = fetch_rosters(conn, stored["date"].min(), stored["date"].max())
                candidates = apply_exception_rules(
                    stored, roster_frame,
                    overtime_threshold_hours=settings.overtime_threshold_hours,
                    roster_tolerance_minutes=settings.roster_tolerance_minutes,
                )
                summary.exceptions_matched = len(candidates)
                summary.exceptions_inserted = insert_exceptions(conn, candidates)
        except Exception:
            transaction.rollback()
            raise

        if dry_run:
            transaction.rollback()
            logger.info("Dry run: all changes rolled back")
        else:
            transaction.commit()
    engine.dispose()
    return summary


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description="Run the attendance ETL.")
    parser.add_argument("--csv", type=Path, default=None,
                        help="attendance export to load (default: $ATTENDANCE_CSV_PATH)")
    parser.add_argument("--dry-run", action="store_true",
                        help="run every step, then roll back")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Command-line entry point. Returns a process exit code."""
    args = parse_args(argv)
    try:
        settings = load_settings()
    except ConfigError as exc:
        configure_logging()
        logger.error("Invalid configuration: %s", exc)
        return 2
    configure_logging(settings.log_level)

    csv_path = args.csv or settings.attendance_csv_path
    logger.info("ETL started: file=%s overtime_threshold=%gh roster_tolerance=%gmin%s",
                csv_path, settings.overtime_threshold_hours,
                settings.roster_tolerance_minutes, " (dry run)" if args.dry_run else "")
    started = time.monotonic()
    try:
        summary = run(settings, csv_path, dry_run=args.dry_run)
    except ExtractError as exc:
        logger.error("ETL failed while reading the export: %s", exc)
        return 1
    except SQLAlchemyError as exc:
        logger.error("ETL failed with a database error; nothing was committed: %s", exc)
        return 1
    except Exception:
        logger.exception("ETL failed unexpectedly; nothing was committed")
        return 1

    logger.info("ETL finished in %.1fs: raw=%d loaded=%d rejected=%d duplicates=%d "
                "exceptions_matched=%d exceptions_new=%d",
                time.monotonic() - started, summary.raw_rows, summary.records_loaded,
                summary.rows_rejected, summary.duplicates_removed,
                summary.exceptions_matched, summary.exceptions_inserted)
    return 0


if __name__ == "__main__":
    sys.exit(main())
