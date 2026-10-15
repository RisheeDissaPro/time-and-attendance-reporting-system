"""Unit tests for the cleaning steps in :mod:`etl.transform`."""

from __future__ import annotations

import pandas as pd

from etl.transform import clean_attendance, compute_hours_worked, parse_with_formats

KNOWN = {1001, 1002, 1003}


def raw(*rows: tuple[str, str, str, str]) -> pd.DataFrame:
    """Build a raw export frame (all strings, like ``read_attendance_csv``)."""
    return pd.DataFrame(rows, columns=["employee_id", "date", "clock_in", "clock_out"])


def test_parses_iso_and_day_first_dates():
    values = pd.Series(["2026-10-05", "05/10/2026", "not a date", None], dtype="string")

    parsed = parse_with_formats(values, ("%Y-%m-%d", "%d/%m/%Y"))

    assert parsed[0] == pd.Timestamp("2026-10-05")
    assert parsed[1] == pd.Timestamp("2026-10-05")
    assert parsed[2:].isna().all()


def test_compute_hours_worked_rounds_to_two_decimals():
    clock_in = pd.Series(pd.to_datetime(["2026-10-05 06:00", "2026-10-05 06:00"]))
    clock_out = pd.Series(pd.to_datetime(["2026-10-05 14:20", pd.NaT]))

    hours = compute_hours_worked(clock_in, clock_out)

    assert hours[0] == 8.33
    assert pd.isna(hours[1])


def test_clean_rows_are_loaded_with_hours():
    result = clean_attendance(raw(("1001", "2026-10-05", "06:00", "14:30")), KNOWN)

    row = result.records.iloc[0]
    assert row["employee_id"] == 1001
    assert row["clock_in"] == pd.Timestamp("2026-10-05 06:00")
    assert row["clock_out"] == pd.Timestamp("2026-10-05 14:30")
    assert row["hours_worked"] == 8.5
    assert result.rejected.empty


def test_overnight_shift_clock_out_moves_to_next_day():
    result = clean_attendance(raw(("1001", "2026-10-05", "22:00", "06:15")), KNOWN)

    row = result.records.iloc[0]
    assert row["clock_out"] == pd.Timestamp("2026-10-06 06:15")
    assert row["hours_worked"] == 8.25


def test_missing_clock_out_is_kept_with_null_hours():
    result = clean_attendance(raw(("1001", "2026-10-05", "06:00", "")), KNOWN)

    assert len(result.records) == 1
    assert pd.isna(result.records.iloc[0]["clock_out"])
    assert pd.isna(result.records.iloc[0]["hours_worked"])


def test_exact_duplicates_are_dropped():
    row = ("1001", "2026-10-05", "06:00", "14:00")

    result = clean_attendance(raw(row, row, row), KNOWN)

    assert len(result.records) == 1
    assert result.exact_duplicates_removed == 2


def test_same_employee_and_day_keeps_last_row():
    result = clean_attendance(raw(("1001", "2026-10-05", "06:00", "14:00"),
                                  ("1001", "05/10/2026", "06:00", "15:00")), KNOWN)

    assert len(result.records) == 1
    assert result.records.iloc[0]["clock_out"] == pd.Timestamp("2026-10-05 15:00")
    assert result.key_duplicates_removed == 1


def test_whitespace_and_seconds_are_handled():
    result = clean_attendance(raw((" 1002 ", " 2026-10-05 ", "06:00:30", " 14:00 ")), KNOWN)

    row = result.records.iloc[0]
    assert row["employee_id"] == 1002
    assert row["clock_in"] == pd.Timestamp("2026-10-05 06:00:30")


def test_unloadable_rows_are_rejected_with_reasons():
    result = clean_attendance(raw(
        ("", "2026-10-05", "06:00", "14:00"),
        ("abc", "2026-10-05", "06:00", "14:00"),
        ("9999", "2026-10-05", "06:00", "14:00"),
        ("1001", "2026-13-45", "06:00", "14:00"),
        ("1002", "2026-10-05", "", ""),
        ("1003", "2026-10-05", "", "14:00"),
        ("1001", "2026-10-06", "06:00", "25:99"),
    ), KNOWN)

    assert result.records.empty
    assert result.rejected["reason"].tolist() == [
        "missing_or_invalid_employee_id",
        "missing_or_invalid_employee_id",
        "unknown_employee",
        "missing_or_invalid_date",
        "no_punches",
        "missing_or_invalid_clock_in",
        "invalid_clock_out",
    ]
    assert result.rejected_by_reason["missing_or_invalid_employee_id"] == 2


def test_empty_export_produces_empty_records():
    result = clean_attendance(raw(), KNOWN)

    assert result.records.empty
    assert result.rejected.empty
