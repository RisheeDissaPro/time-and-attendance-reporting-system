"""Shared fixtures and builders for the ETL unit tests."""

from __future__ import annotations

from typing import Callable

import pandas as pd
import pytest

SHIFT_DATE = "2026-10-05"


def _ts(value: str | None) -> pd.Timestamp:
    """Parse ``value`` to a timestamp (``NaT`` for ``None``)."""
    return pd.Timestamp(value) if value is not None else pd.NaT


@pytest.fixture
def make_record() -> Callable[..., dict]:
    """Build one attendance record; ``hours_worked`` is derived from the punches."""
    counter = iter(range(1, 10_000))

    def _make(clock_in: str = f"{SHIFT_DATE} 06:00", clock_out: str | None = f"{SHIFT_DATE} 14:00",
              employee_id: int = 1001, date: str = SHIFT_DATE,
              record_id: int | None = None) -> dict:
        start, end = _ts(clock_in), _ts(clock_out)
        hours = round((end - start).total_seconds() / 3600, 2) if end is not pd.NaT else None
        return {
            "record_id": record_id if record_id is not None else next(counter),
            "employee_id": employee_id,
            "date": pd.Timestamp(date),
            "clock_in": start,
            "clock_out": end,
            "hours_worked": hours,
        }

    return _make


@pytest.fixture
def records_frame() -> Callable[[list[dict]], pd.DataFrame]:
    """Turn a list of record dicts into a frame with the loader's dtypes."""
    def _frame(rows: list[dict]) -> pd.DataFrame:
        frame = pd.DataFrame(rows, columns=["record_id", "employee_id", "date",
                                            "clock_in", "clock_out", "hours_worked"])
        frame["clock_out"] = pd.to_datetime(frame["clock_out"])
        frame["hours_worked"] = pd.to_numeric(frame["hours_worked"])
        return frame

    return _frame


@pytest.fixture
def roster() -> Callable[..., pd.DataFrame]:
    """Build a roster frame from (employee_id, shift_date, start, end) tuples."""
    def _roster(*shifts: tuple[int, str, str, str]) -> pd.DataFrame:
        return pd.DataFrame(
            [{"employee_id": emp, "shift_date": pd.Timestamp(day).date(),
              "scheduled_start": pd.Timestamp(start), "scheduled_end": pd.Timestamp(end)}
             for emp, day, start, end in shifts],
            columns=["employee_id", "shift_date", "scheduled_start", "scheduled_end"],
        )

    return _roster


@pytest.fixture
def day_roster(roster: Callable[..., pd.DataFrame]) -> pd.DataFrame:
    """Employee 1001 rostered 06:00-14:00 on :data:`SHIFT_DATE`."""
    return roster((1001, SHIFT_DATE, f"{SHIFT_DATE} 06:00", f"{SHIFT_DATE} 14:00"))
