"""Exception rules applied to cleaned attendance records.

Each rule takes attendance records (with ``record_id``) and returns candidate
exceptions with the columns in :data:`EXCEPTION_COLUMNS`. Rules are pure
functions, so thresholds can be tested without a database.

Rules:

* ``missed_clock_in`` - the record has no clock-out (``clock_out`` is null).
* ``overtime``        - ``hours_worked`` is above the overtime threshold.
* ``roster_breach``   - clock-in or clock-out differs from the rostered start
  or end by more than the tolerance, or the shift was not rostered at all.
"""

from __future__ import annotations

import logging

import pandas as pd

logger = logging.getLogger(__name__)

MISSED_CLOCK_IN = "missed_clock_in"
OVERTIME = "overtime"
ROSTER_BREACH = "roster_breach"
EXCEPTION_COLUMNS = ["employee_id", "record_id", "type", "date", "details"]


def _empty() -> pd.DataFrame:
    """Return an empty exceptions frame with the expected columns."""
    return pd.DataFrame(columns=EXCEPTION_COLUMNS)


def _as_exceptions(rows: pd.DataFrame, exception_type: str, details: pd.Series) -> pd.DataFrame:
    """Shape matching records into exception rows."""
    if rows.empty:
        return _empty()
    return pd.DataFrame({
        "employee_id": rows["employee_id"].astype("int64"),
        "record_id": rows["record_id"].astype("int64"),
        "type": exception_type,
        "date": rows["date"],
        "details": details,
    }).reset_index(drop=True)


def flag_missed_clock_in(records: pd.DataFrame) -> pd.DataFrame:
    """Flag records whose ``clock_out`` is missing.

    Args:
        records: Attendance records with ``record_id``, ``employee_id``, ``date``,
            ``clock_out``.

    Returns:
        One ``missed_clock_in`` exception per record without a clock-out.
    """
    matches = records[records["clock_out"].isna()]
    return _as_exceptions(matches, MISSED_CLOCK_IN,
                          pd.Series("No clock-out recorded", index=matches.index))


def flag_overtime(records: pd.DataFrame, threshold_hours: float) -> pd.DataFrame:
    """Flag records where ``hours_worked`` is strictly above ``threshold_hours``.

    Args:
        records: Attendance records with ``hours_worked`` (``NaN`` never matches).
        threshold_hours: Overtime threshold in hours.

    Returns:
        One ``overtime`` exception per matching record.
    """
    hours = pd.to_numeric(records["hours_worked"], errors="coerce")
    matches = records[hours > threshold_hours]
    details = hours[matches.index].map(
        lambda h: f"Worked {h:.2f} h (threshold {threshold_hours:g} h)")
    return _as_exceptions(matches, OVERTIME, details)


def _describe_deviation(label: str, minutes: float) -> str:
    """Describe a punch deviation such as ``clock-in 45 min late``."""
    if label == "clock-in":
        direction = "late" if minutes > 0 else "early"
    else:
        direction = "after roster end" if minutes > 0 else "before roster end"
    return f"{label} {abs(minutes):.0f} min {direction}"


def flag_roster_breach(records: pd.DataFrame, rosters: pd.DataFrame,
                       tolerance_minutes: float) -> pd.DataFrame:
    """Flag records that deviate from the roster by more than ``tolerance_minutes``.

    A record breaches the roster when the clock-in differs from
    ``scheduled_start``, or the clock-out differs from ``scheduled_end``, by
    strictly more than the tolerance (early or late). A missing clock-out is
    not compared (it is covered by ``missed_clock_in``). A record with no
    roster entry for that employee and date is also a breach.

    Args:
        records: Attendance records with ``record_id``, ``employee_id``, ``date``,
            ``clock_in``, ``clock_out``.
        rosters: Rosters with ``employee_id``, ``shift_date``, ``scheduled_start``,
            ``scheduled_end``.
        tolerance_minutes: Allowed deviation in minutes.

    Returns:
        One ``roster_breach`` exception per matching record.
    """
    if records.empty:
        return _empty()

    roster_cols = rosters.loc[:, ["employee_id", "shift_date",
                                  "scheduled_start", "scheduled_end"]].copy()
    roster_cols["shift_date"] = pd.to_datetime(roster_cols["shift_date"])
    roster_cols = roster_cols.drop_duplicates(subset=["employee_id", "shift_date"], keep="last")
    left = records.copy()
    left["_join_date"] = pd.to_datetime(left["date"])
    merged = left.merge(roster_cols, how="left",
                        left_on=["employee_id", "_join_date"],
                        right_on=["employee_id", "shift_date"])
    merged.index = records.index

    start_dev = (pd.to_datetime(merged["clock_in"])
                 - pd.to_datetime(merged["scheduled_start"])).dt.total_seconds() / 60
    end_dev = (pd.to_datetime(merged["clock_out"])
               - pd.to_datetime(merged["scheduled_end"])).dt.total_seconds() / 60

    unrostered = merged["scheduled_start"].isna()
    start_breach = start_dev.abs() > tolerance_minutes
    end_breach = end_dev.abs() > tolerance_minutes
    is_breach = unrostered | start_breach | end_breach

    details: dict[int, str] = {}
    for idx in merged.index[is_breach]:
        if unrostered[idx]:
            details[idx] = "Worked without a rostered shift"
            continue
        parts = []
        if start_breach[idx]:
            parts.append(_describe_deviation("clock-in", start_dev[idx]))
        if end_breach[idx]:
            parts.append(_describe_deviation("clock-out", end_dev[idx]))
        details[idx] = "; ".join(parts) + f" (tolerance {tolerance_minutes:g} min)"

    matches = records[is_breach]
    return _as_exceptions(matches, ROSTER_BREACH, pd.Series(details, dtype="object"))


def apply_exception_rules(records: pd.DataFrame, rosters: pd.DataFrame,
                          overtime_threshold_hours: float,
                          roster_tolerance_minutes: float) -> pd.DataFrame:
    """Run every rule and combine the results.

    Args:
        records: Loaded attendance records (must include ``record_id``).
        rosters: Rosters covering the same dates.
        overtime_threshold_hours: Threshold for :func:`flag_overtime`.
        roster_tolerance_minutes: Tolerance for :func:`flag_roster_breach`.

    Returns:
        All candidate exceptions, at most one per (record_id, type).
    """
    results = [
        flag_missed_clock_in(records),
        flag_overtime(records, overtime_threshold_hours),
        flag_roster_breach(records, rosters, roster_tolerance_minutes),
    ]
    non_empty = [frame for frame in results if not frame.empty]
    combined = pd.concat(non_empty, ignore_index=True) if non_empty else _empty()
    counts = combined["type"].value_counts().to_dict() if len(combined) else {}
    logger.info("Exception rules matched %d record(s): %s", len(combined),
                ", ".join(f"{k}={v}" for k, v in sorted(counts.items())) or "none")
    return combined
