"""Transform: clean raw attendance rows and compute ``hours_worked``.

Everything here is a pure pandas function so it can be unit tested without a
database.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass, field

import pandas as pd

logger = logging.getLogger(__name__)

DATE_FORMATS: tuple[str, ...] = ("%Y-%m-%d", "%d/%m/%Y")
TIME_FORMATS: tuple[str, ...] = ("%H:%M", "%H:%M:%S")
RECORD_COLUMNS = ["employee_id", "date", "clock_in", "clock_out", "hours_worked"]


@dataclass
class TransformResult:
    """Output of :func:`clean_attendance`."""

    records: pd.DataFrame
    rejected: pd.DataFrame
    exact_duplicates_removed: int = 0
    key_duplicates_removed: int = 0
    rejected_by_reason: dict[str, int] = field(default_factory=dict)


def _blank_to_na(series: pd.Series) -> pd.Series:
    """Strip whitespace and turn empty strings into ``NA``."""
    stripped = series.astype("string").str.strip()
    return stripped.mask(stripped == "")


def parse_with_formats(values: pd.Series, formats: Iterable[str]) -> pd.Series:
    """Parse strings to datetimes, trying each explicit format in order.

    Explicit formats avoid pandas guessing (and silently swapping day and
    month on values such as ``03/04/2026``).

    Args:
        values: String values; ``NA`` stays ``NaT``.
        formats: ``strptime`` formats, tried in order; the first match wins.

    Returns:
        A datetime series, ``NaT`` where no format matched.
    """
    result = pd.Series(pd.NaT, index=values.index, dtype="datetime64[ns]")
    for fmt in formats:
        pending = result.isna() & values.notna()
        if not pending.any():
            break
        result.loc[pending] = pd.to_datetime(values[pending], format=fmt, errors="coerce")
    return result


def parse_time_of_day(values: pd.Series) -> pd.Series:
    """Parse ``HH:MM`` / ``HH:MM:SS`` strings to a time-since-midnight timedelta."""
    parsed = parse_with_formats(values, TIME_FORMATS)
    return parsed - parsed.dt.normalize()


def compute_hours_worked(clock_in: pd.Series, clock_out: pd.Series) -> pd.Series:
    """Return shift length in hours, rounded to 2 decimals (``NaN`` if a punch is missing)."""
    return ((clock_out - clock_in).dt.total_seconds() / 3600).round(2)


def clean_attendance(raw: pd.DataFrame, valid_employee_ids: Iterable[int]) -> TransformResult:
    """Clean a raw attendance export into rows ready for ``attendance_records``.

    Steps:

    1. Trim whitespace; treat blank cells as missing.
    2. Drop exact duplicate rows.
    3. Parse ``employee_id``, ``date`` (ISO or DD/MM/YYYY) and punch times.
    4. Reject rows that cannot be loaded: bad or unknown employee, bad date,
       no clock-in, or an unparseable clock-out.
    5. Build clock-in/out timestamps; a clock-out earlier than the clock-in is
       an overnight shift and moves to the next day.
    6. Keep the last row per (employee_id, date), since later rows are corrections.
    7. Compute ``hours_worked``.

    Args:
        raw: Frame with string columns ``employee_id``, ``date``, ``clock_in``, ``clock_out``.
        valid_employee_ids: Employee IDs present in the ``employees`` table.

    Returns:
        Cleaned records, rejected rows (with a ``reason`` column) and counters.
    """
    df = raw.loc[:, ["employee_id", "date", "clock_in", "clock_out"]].apply(_blank_to_na)

    before = len(df)
    df = df.drop_duplicates()
    exact_duplicates = before - len(df)

    employee_id = pd.to_numeric(df["employee_id"], errors="coerce")
    is_integral = employee_id.notna() & (employee_id % 1 == 0)
    df["employee_id_parsed"] = employee_id.where(is_integral).astype("Int64")
    df["date_parsed"] = parse_with_formats(df["date"], DATE_FORMATS).dt.normalize()
    df["in_time"] = parse_time_of_day(df["clock_in"])
    df["out_time"] = parse_time_of_day(df["clock_out"])

    known_ids = set(int(i) for i in valid_employee_ids)
    reason = pd.Series(pd.NA, index=df.index, dtype="string")
    checks = [
        ("missing_or_invalid_employee_id", df["employee_id_parsed"].isna()),
        ("unknown_employee", ~df["employee_id_parsed"].isin(known_ids)),
        ("missing_or_invalid_date", df["date_parsed"].isna()),
        ("no_punches", df["clock_in"].isna() & df["clock_out"].isna()),
        ("missing_or_invalid_clock_in", df["in_time"].isna()),
        ("invalid_clock_out", df["clock_out"].notna() & df["out_time"].isna()),
    ]
    for label, failed in checks:
        reason = reason.mask(reason.isna() & failed.fillna(True).astype(bool), label)

    rejected = raw.loc[reason[reason.notna()].index].assign(reason=reason.dropna())
    rejected_by_reason = rejected["reason"].value_counts().to_dict() if len(rejected) else {}
    for label, count in rejected_by_reason.items():
        logger.warning("Rejected %d row(s): %s", count, label)

    good = df[reason.isna()].copy()
    clock_in = good["date_parsed"] + good["in_time"]
    clock_out = good["date_parsed"] + good["out_time"]
    overnight = clock_out.notna() & (clock_out < clock_in)
    clock_out = clock_out.mask(overnight, clock_out + pd.Timedelta(days=1))

    records = pd.DataFrame({
        "employee_id": good["employee_id_parsed"].astype("int64"),
        "date": good["date_parsed"],
        "clock_in": clock_in,
        "clock_out": clock_out,
    })
    before = len(records)
    records = records.drop_duplicates(subset=["employee_id", "date"], keep="last")
    key_duplicates = before - len(records)
    if key_duplicates:
        logger.warning("Kept the last of %d conflicting row(s) for the same employee and date",
                       key_duplicates)

    records["hours_worked"] = compute_hours_worked(records["clock_in"], records["clock_out"])
    records = records.sort_values(["date", "employee_id"]).reset_index(drop=True)

    logger.info("Cleaned %d rows: %d records, %d rejected, %d exact duplicates, "
                "%d overnight shifts", len(raw), len(records), len(rejected),
                exact_duplicates, int(overnight.sum()))
    return TransformResult(
        records=records[RECORD_COLUMNS],
        rejected=rejected,
        exact_duplicates_removed=exact_duplicates,
        key_duplicates_removed=key_duplicates,
        rejected_by_reason=rejected_by_reason,
    )
