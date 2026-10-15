"""Load: idempotent writes to ``attendance_records`` and ``exceptions``."""

from __future__ import annotations

import logging
import math
from datetime import date, datetime
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Connection

from etl.db import attendance_records, employees, exceptions, rosters

logger = logging.getLogger(__name__)

CHUNK_SIZE = 1000


def _to_python(value: Any) -> Any:
    """Convert pandas/numpy scalars to plain Python values psycopg2 can adapt."""
    if value is None or value is pd.NaT or value is pd.NA:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    if isinstance(value, pd.Timestamp):
        return value.to_pydatetime()
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.floating):
        return float(value)
    return value


def _records(frame: pd.DataFrame) -> list[dict[str, Any]]:
    """Turn a frame into a list of row dicts with Python-native values."""
    return [{key: _to_python(val) for key, val in row.items()}
            for row in frame.to_dict("records")]


def _as_date(value: Any) -> date:
    """Normalise a timestamp/datetime/date to a ``date``."""
    if isinstance(value, datetime):
        return value.date()
    return value


def fetch_employee_ids(conn: Connection) -> set[int]:
    """Return every employee ID in the ``employees`` table."""
    return set(conn.execute(select(employees.c.employee_id)).scalars())


def fetch_rosters(conn: Connection, start: date, end: date) -> pd.DataFrame:
    """Return rosters with ``shift_date`` between ``start`` and ``end`` inclusive."""
    stmt = (select(rosters.c.employee_id, rosters.c.shift_date,
                   rosters.c.scheduled_start, rosters.c.scheduled_end)
            .where(rosters.c.shift_date.between(start, end)))
    rows = conn.execute(stmt).mappings().all()
    return pd.DataFrame(rows, columns=["employee_id", "shift_date",
                                       "scheduled_start", "scheduled_end"])


def upsert_attendance(conn: Connection, records: pd.DataFrame) -> pd.DataFrame:
    """Insert or update cleaned records, keyed on (employee_id, date).

    Re-running with the same data updates rows in place, so nothing is
    duplicated; corrected punches in a later export overwrite earlier ones.

    Args:
        conn: Open connection inside the ETL transaction.
        records: Output of :func:`etl.transform.clean_attendance`.

    Returns:
        The stored rows, including their ``record_id``.
    """
    returned: list[dict[str, Any]] = []
    rows = _records(records)
    for row in rows:
        row["date"] = _as_date(row["date"])

    for start in range(0, len(rows), CHUNK_SIZE):
        stmt = pg_insert(attendance_records).values(rows[start:start + CHUNK_SIZE])
        stmt = stmt.on_conflict_do_update(
            index_elements=["employee_id", "date"],
            set_={
                "clock_in": stmt.excluded.clock_in,
                "clock_out": stmt.excluded.clock_out,
                "hours_worked": stmt.excluded.hours_worked,
                "loaded_at": func.now(),
            },
        ).returning(
            attendance_records.c.record_id, attendance_records.c.employee_id,
            attendance_records.c.date, attendance_records.c.clock_in,
            attendance_records.c.clock_out, attendance_records.c.hours_worked,
        )
        returned.extend(dict(r) for r in conn.execute(stmt).mappings())

    logger.info("Upserted %d attendance records", len(returned))
    stored = pd.DataFrame(returned, columns=["record_id", "employee_id", "date",
                                             "clock_in", "clock_out", "hours_worked"])
    stored["hours_worked"] = pd.to_numeric(stored["hours_worked"], errors="coerce")
    return stored


def insert_exceptions(conn: Connection, candidates: pd.DataFrame) -> int:
    """Insert new exceptions, skipping any (record_id, type) that already exists.

    Existing exceptions are left untouched, so a status set by a supervisor
    (``resolved`` / ``escalated``) is never reset by a re-run.

    Args:
        conn: Open connection inside the ETL transaction.
        candidates: Output of :func:`etl.rules.apply_exception_rules`.

    Returns:
        Number of newly inserted exceptions.
    """
    if candidates.empty:
        return 0
    rows = _records(candidates)
    for row in rows:
        row["date"] = _as_date(row["date"])

    inserted = 0
    for start in range(0, len(rows), CHUNK_SIZE):
        stmt = (pg_insert(exceptions).values(rows[start:start + CHUNK_SIZE])
                .on_conflict_do_nothing(index_elements=["record_id", "type"]))
        inserted += conn.execute(stmt).rowcount
    logger.info("Inserted %d new exceptions (%d already existed)",
                inserted, len(rows) - inserted)
    return inserted
