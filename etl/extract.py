"""Extract: read the attendance CSV export and land it, unchanged, in staging."""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

import pandas as pd
from sqlalchemy import delete, insert
from sqlalchemy.engine import Connection

from etl.db import raw_attendance

logger = logging.getLogger(__name__)

RAW_COLUMNS = ["employee_id", "date", "clock_in", "clock_out"]
CHUNK_SIZE = 1000


class ExtractError(RuntimeError):
    """Raised when the attendance export cannot be read or has the wrong shape."""


def file_checksum(path: Path) -> str:
    """Return the SHA-256 hex digest of a file's contents."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 16), b""):
            digest.update(block)
    return digest.hexdigest()


def read_attendance_csv(path: Path) -> pd.DataFrame:
    """Read the timeclock export with every value kept as the original string.

    Args:
        path: Path to the CSV export.

    Returns:
        A frame with exactly :data:`RAW_COLUMNS`, blanks as empty strings.

    Raises:
        ExtractError: If the file is missing, unreadable or lacks required columns.
    """
    if not path.is_file():
        raise ExtractError(f"Attendance export not found: {path}")
    try:
        frame = pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8")
    except (pd.errors.ParserError, pd.errors.EmptyDataError, UnicodeDecodeError) as exc:
        raise ExtractError(f"Could not parse {path}: {exc}") from exc

    frame.columns = [str(col).strip().lower() for col in frame.columns]
    missing = [col for col in RAW_COLUMNS if col not in frame.columns]
    if missing:
        raise ExtractError(f"{path.name} is missing required columns: {', '.join(missing)}")
    logger.info("Read %d rows from %s", len(frame), path)
    return frame[RAW_COLUMNS].copy()


def stage_raw_attendance(conn: Connection, raw: pd.DataFrame,
                         source_file: str, checksum: str) -> int:
    """Store the raw rows in ``staging.raw_attendance``.

    Rows previously staged from a file with the same checksum are replaced, so
    re-running the same export does not duplicate raw data.

    Args:
        conn: Open connection inside the ETL transaction.
        raw: Frame returned by :func:`read_attendance_csv`.
        source_file: File name recorded for lineage.
        checksum: SHA-256 of the file.

    Returns:
        Number of rows staged.
    """
    replaced = conn.execute(
        delete(raw_attendance).where(raw_attendance.c.file_checksum == checksum)
    ).rowcount
    if replaced:
        logger.info("Replacing %d previously staged rows for this file", replaced)

    rows = [
        {"source_file": source_file, "file_checksum": checksum, "row_number": index + 1,
         **record}
        for index, record in enumerate(raw.to_dict("records"))
    ]
    for start in range(0, len(rows), CHUNK_SIZE):
        conn.execute(insert(raw_attendance), rows[start:start + CHUNK_SIZE])
    logger.info("Staged %d raw rows from %s", len(rows), source_file)
    return len(rows)
