"""Load reference data (departments, employees, rosters) from CSV into Postgres.

Every table is upserted, so the script is safe to re-run: changed rows are
updated in place and nothing is duplicated.

Usage::

    python -m db.seed [--data-dir data]
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import Table
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Connection
from sqlalchemy.exc import SQLAlchemyError

from etl.config import ConfigError, Settings, load_settings
from etl.db import create_db_engine, departments, employees, rosters
from etl.logging_config import configure_logging

logger = logging.getLogger(__name__)

CHUNK_SIZE = 1000


class SeedError(RuntimeError):
    """Raised when a reference CSV is missing or malformed."""


def _read_csv(path: Path, required: list[str]) -> pd.DataFrame:
    """Read a reference CSV and check that its required columns are present."""
    if not path.is_file():
        raise SeedError(f"Reference file not found: {path}")
    frame = pd.read_csv(path, dtype=str, keep_default_na=False)
    missing = sorted(set(required) - set(frame.columns))
    if missing:
        raise SeedError(f"{path.name} is missing columns: {', '.join(missing)}")
    return frame.apply(lambda col: col.str.strip())


def _upsert(conn: Connection, table: Table, rows: list[dict[str, Any]],
            conflict_columns: list[str]) -> None:
    """Insert ``rows`` into ``table``, updating non-key columns on conflict."""
    for start in range(0, len(rows), CHUNK_SIZE):
        chunk = rows[start:start + CHUNK_SIZE]
        stmt = pg_insert(table).values(chunk)
        update_cols = {key: stmt.excluded[key] for key in chunk[0] if key not in conflict_columns}
        conn.execute(stmt.on_conflict_do_update(index_elements=conflict_columns,
                                                set_=update_cols))
    logger.info("Upserted %d rows into %s", len(rows), table.name)


def load_departments(conn: Connection, data_dir: Path) -> None:
    """Upsert ``departments.csv``."""
    frame = _read_csv(data_dir / "departments.csv", ["department_id", "name", "business_unit"])
    rows = [{"department_id": int(r.department_id), "name": r.name,
             "business_unit": r.business_unit} for r in frame.itertuples()]
    _upsert(conn, departments, rows, ["department_id"])


def load_employees(conn: Connection, data_dir: Path) -> None:
    """Upsert ``employees.csv``, managers first so the self-reference resolves."""
    frame = _read_csv(data_dir / "employees.csv",
                      ["employee_id", "name", "department_id", "role", "manager_id"])
    rows = [{"employee_id": int(r.employee_id), "name": r.name,
             "department_id": int(r.department_id), "role": r.role,
             "manager_id": int(r.manager_id) if r.manager_id else None}
            for r in frame.itertuples()]
    rows.sort(key=lambda row: row["manager_id"] is not None)
    _upsert(conn, employees, rows, ["employee_id"])


def load_rosters(conn: Connection, data_dir: Path) -> None:
    """Upsert ``rosters.csv`` keyed on (employee_id, shift_date)."""
    frame = _read_csv(data_dir / "rosters.csv",
                      ["employee_id", "shift_date", "scheduled_start", "scheduled_end"])
    try:
        rows = [{"employee_id": int(r.employee_id),
                 "shift_date": pd.Timestamp(r.shift_date).date(),
                 "scheduled_start": pd.Timestamp(r.scheduled_start).to_pydatetime(),
                 "scheduled_end": pd.Timestamp(r.scheduled_end).to_pydatetime()}
                for r in frame.itertuples()]
    except ValueError as exc:
        raise SeedError(f"rosters.csv contains an unparseable value: {exc}") from exc
    _upsert(conn, rosters, rows, ["employee_id", "shift_date"])


def seed(settings: Settings, data_dir: Path) -> None:
    """Load all reference data in a single transaction.

    Args:
        settings: Database connection settings.
        data_dir: Directory containing the reference CSV files.
    """
    engine = create_db_engine(settings)
    with engine.begin() as conn:
        load_departments(conn, data_dir)
        load_employees(conn, data_dir)
        load_rosters(conn, data_dir)


def main(argv: list[str] | None = None) -> int:
    """Command-line entry point. Returns a process exit code."""
    parser = argparse.ArgumentParser(description="Load reference data into Postgres.")
    parser.add_argument("--data-dir", type=Path, default=None,
                        help="directory with the reference CSVs (default: $DATA_DIR)")
    args = parser.parse_args(argv)

    try:
        settings = load_settings()
    except ConfigError as exc:
        configure_logging()
        logger.error("Invalid configuration: %s", exc)
        return 2
    configure_logging(settings.log_level)

    try:
        seed(settings, args.data_dir or settings.data_dir)
    except SeedError as exc:
        logger.error("Seeding failed: %s", exc)
        return 1
    except SQLAlchemyError as exc:
        logger.error("Seeding failed with a database error; nothing was committed: %s", exc)
        return 1
    logger.info("Reference data loaded")
    return 0


if __name__ == "__main__":
    sys.exit(main())
