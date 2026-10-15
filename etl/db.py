"""Database engine creation and SQLAlchemy Core table definitions.

The tables mirror ``db/schema.sql``, which is the source of truth for DDL;
they are never created from Python.
"""

from __future__ import annotations

import logging
import time

from sqlalchemy import (
    BigInteger, Column, Date, DateTime, Integer, MetaData, Numeric, Table, Text,
    create_engine, text,
)
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError

from etl.config import Settings

logger = logging.getLogger(__name__)

metadata = MetaData()

raw_attendance = Table(
    "raw_attendance", metadata,
    Column("raw_id", BigInteger, primary_key=True),
    Column("source_file", Text, nullable=False),
    Column("file_checksum", Text, nullable=False),
    Column("row_number", Integer, nullable=False),
    Column("employee_id", Text),
    Column("date", Text),
    Column("clock_in", Text),
    Column("clock_out", Text),
    Column("loaded_at", DateTime(timezone=True)),
    schema="staging",
)

departments = Table(
    "departments", metadata,
    Column("department_id", Integer, primary_key=True),
    Column("name", Text, nullable=False),
    Column("business_unit", Text, nullable=False),
)

employees = Table(
    "employees", metadata,
    Column("employee_id", Integer, primary_key=True),
    Column("name", Text, nullable=False),
    Column("department_id", Integer, nullable=False),
    Column("role", Text, nullable=False),
    Column("manager_id", Integer),
)

rosters = Table(
    "rosters", metadata,
    Column("roster_id", BigInteger, primary_key=True),
    Column("employee_id", Integer, nullable=False),
    Column("scheduled_start", DateTime, nullable=False),
    Column("scheduled_end", DateTime, nullable=False),
    Column("shift_date", Date, nullable=False),
)

attendance_records = Table(
    "attendance_records", metadata,
    Column("record_id", BigInteger, primary_key=True),
    Column("employee_id", Integer, nullable=False),
    Column("clock_in", DateTime, nullable=False),
    Column("clock_out", DateTime),
    Column("date", Date, nullable=False),
    Column("hours_worked", Numeric(5, 2)),
    Column("loaded_at", DateTime(timezone=True)),
)

exceptions = Table(
    "exceptions", metadata,
    Column("exception_id", BigInteger, primary_key=True),
    Column("employee_id", Integer, nullable=False),
    Column("record_id", BigInteger, nullable=False),
    Column("type", Text, nullable=False),
    Column("date", Date, nullable=False),
    Column("status", Text, nullable=False),
    Column("details", Text),
    Column("created_at", DateTime(timezone=True)),
    Column("updated_at", DateTime(timezone=True)),
)


def create_db_engine(settings: Settings) -> Engine:
    """Create an engine and wait until the database accepts connections.

    Retries with linear backoff so the ETL can start before Postgres is ready.

    Args:
        settings: Settings holding the database URL and retry policy.

    Returns:
        A connected SQLAlchemy engine.

    Raises:
        OperationalError: If the database is still unreachable after all retries.
    """
    engine = create_engine(settings.database_url, pool_pre_ping=True)
    attempt = 1
    while True:
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            logger.debug("Connected to database on attempt %d", attempt)
            return engine
        except OperationalError as exc:
            if attempt >= settings.db_connect_retries:
                logger.error("Database unreachable after %d attempts", attempt)
                raise
            delay = settings.db_connect_delay_seconds * attempt
            logger.warning("Database not ready (attempt %d/%d): %s; retrying in %.0fs",
                           attempt, settings.db_connect_retries,
                           str(exc.orig).strip(), delay)
            time.sleep(delay)
            attempt += 1
