"""Unit tests for the exception rules in :mod:`etl.rules`."""

from __future__ import annotations

import pandas as pd
import pytest

from etl.rules import (
    EXCEPTION_COLUMNS, MISSED_CLOCK_IN, OVERTIME, ROSTER_BREACH, apply_exception_rules,
    flag_missed_clock_in, flag_overtime, flag_roster_breach,
)
from tests.conftest import SHIFT_DATE

D = SHIFT_DATE


# --------------------------------------------------------------------- missed_clock_in

class TestMissedClockIn:
    def test_flags_record_without_clock_out(self, make_record, records_frame):
        records = records_frame([make_record(clock_out=None, record_id=7)])

        result = flag_missed_clock_in(records)

        assert len(result) == 1
        assert result.loc[0, "type"] == MISSED_CLOCK_IN
        assert result.loc[0, "record_id"] == 7
        assert result.loc[0, "employee_id"] == 1001

    def test_ignores_complete_records(self, make_record, records_frame):
        records = records_frame([make_record()])

        assert flag_missed_clock_in(records).empty

    def test_only_incomplete_records_are_flagged(self, make_record, records_frame):
        records = records_frame([
            make_record(record_id=1),
            make_record(record_id=2, clock_out=None, employee_id=1002),
            make_record(record_id=3, employee_id=1003),
        ])

        assert flag_missed_clock_in(records)["record_id"].tolist() == [2]


# --------------------------------------------------------------------------- overtime

class TestOvertime:
    @pytest.mark.parametrize(("clock_out", "expected"), [
        (f"{D} 16:00", False),        # exactly 10.00 h: not above the threshold
        (f"{D} 16:01", True),         # 10.02 h
        (f"{D} 14:00", False),        # normal 8 h shift
        (f"{D} 19:30", True),         # 13.5 h
    ])
    def test_default_threshold_boundary(self, make_record, records_frame, clock_out, expected):
        records = records_frame([make_record(clock_out=clock_out)])

        assert (not flag_overtime(records, threshold_hours=10).empty) is expected

    def test_strictly_greater_than_threshold(self, records_frame):
        records = records_frame([
            {"record_id": 1, "employee_id": 1001, "date": pd.Timestamp(D),
             "clock_in": pd.Timestamp(f"{D} 06:00"), "clock_out": pd.Timestamp(f"{D} 16:00"),
             "hours_worked": 10.00},
            {"record_id": 2, "employee_id": 1002, "date": pd.Timestamp(D),
             "clock_in": pd.Timestamp(f"{D} 06:00"), "clock_out": pd.Timestamp(f"{D} 16:01"),
             "hours_worked": 10.01},
        ])

        assert flag_overtime(records, threshold_hours=10)["record_id"].tolist() == [2]

    def test_custom_threshold(self, make_record, records_frame):
        records = records_frame([make_record(clock_out=f"{D} 15:00")])  # 9 h

        assert flag_overtime(records, threshold_hours=10).empty
        assert len(flag_overtime(records, threshold_hours=8.5)) == 1

    def test_missing_hours_never_flagged(self, make_record, records_frame):
        records = records_frame([make_record(clock_out=None)])

        assert flag_overtime(records, threshold_hours=0.1).empty

    def test_overnight_shift_uses_hours_worked(self, make_record, records_frame):
        record = make_record(clock_in=f"{D} 22:00", clock_out="2026-10-06 09:00")  # 11 h
        result = flag_overtime(records_frame([record]), threshold_hours=10)

        assert result.loc[0, "type"] == OVERTIME
        assert "11.00 h" in result.loc[0, "details"]


# ---------------------------------------------------------------------- roster_breach

class TestRosterBreach:
    @pytest.mark.parametrize(("clock_in", "expected"), [
        (f"{D} 06:30", False),   # exactly 30 min late: within tolerance
        (f"{D} 06:31", True),    # 31 min late
        (f"{D} 05:30", False),   # exactly 30 min early
        (f"{D} 05:29", True),    # 31 min early
        (f"{D} 06:05", False),   # normal jitter
    ])
    def test_clock_in_tolerance_boundary(self, make_record, records_frame, day_roster,
                                         clock_in, expected):
        records = records_frame([make_record(clock_in=clock_in)])

        result = flag_roster_breach(records, day_roster, tolerance_minutes=30)

        assert (not result.empty) is expected

    @pytest.mark.parametrize(("clock_out", "expected"), [
        (f"{D} 14:30", False),
        (f"{D} 14:31", True),
        (f"{D} 13:29", True),    # left 31 min early
    ])
    def test_clock_out_tolerance_boundary(self, make_record, records_frame, day_roster,
                                          clock_out, expected):
        records = records_frame([make_record(clock_out=clock_out)])

        result = flag_roster_breach(records, day_roster, tolerance_minutes=30)

        assert (not result.empty) is expected

    def test_details_describe_direction(self, make_record, records_frame, day_roster):
        records = records_frame([make_record(clock_in=f"{D} 06:45", clock_out=f"{D} 16:00")])

        details = flag_roster_breach(records, day_roster, tolerance_minutes=30).loc[0, "details"]

        assert "clock-in 45 min late" in details
        assert "clock-out 120 min after roster end" in details

    def test_custom_tolerance(self, make_record, records_frame, day_roster):
        records = records_frame([make_record(clock_in=f"{D} 06:20")])

        assert flag_roster_breach(records, day_roster, tolerance_minutes=30).empty
        assert len(flag_roster_breach(records, day_roster, tolerance_minutes=15)) == 1

    def test_unrostered_shift_is_breach(self, make_record, records_frame, day_roster):
        records = records_frame([make_record(employee_id=1002)])

        result = flag_roster_breach(records, day_roster, tolerance_minutes=30)

        assert result.loc[0, "type"] == ROSTER_BREACH
        assert result.loc[0, "details"] == "Worked without a rostered shift"

    def test_roster_on_other_day_does_not_match(self, make_record, records_frame, day_roster):
        records = records_frame([make_record(date="2026-10-06", clock_in="2026-10-06 06:00",
                                             clock_out="2026-10-06 14:00")])

        result = flag_roster_breach(records, day_roster, tolerance_minutes=30)

        assert result.loc[0, "details"] == "Worked without a rostered shift"

    def test_missing_clock_out_only_checks_clock_in(self, make_record, records_frame,
                                                    day_roster):
        on_time = records_frame([make_record(clock_out=None)])
        late = records_frame([make_record(clock_in=f"{D} 07:00", clock_out=None)])

        assert flag_roster_breach(on_time, day_roster, tolerance_minutes=30).empty
        assert len(flag_roster_breach(late, day_roster, tolerance_minutes=30)) == 1

    def test_overnight_shift_within_tolerance(self, make_record, records_frame, roster):
        night = roster((1001, D, f"{D} 22:00", "2026-10-06 06:00"))
        records = records_frame([make_record(clock_in=f"{D} 21:55",
                                             clock_out="2026-10-06 06:10")])

        assert flag_roster_breach(records, night, tolerance_minutes=30).empty

    def test_overnight_shift_late_clock_out(self, make_record, records_frame, roster):
        night = roster((1001, D, f"{D} 22:00", "2026-10-06 06:00"))
        records = records_frame([make_record(clock_in=f"{D} 22:00",
                                             clock_out="2026-10-06 07:00")])

        assert len(flag_roster_breach(records, night, tolerance_minutes=30)) == 1

    def test_empty_rosters_flag_everything(self, make_record, records_frame, roster):
        records = records_frame([make_record(record_id=1), make_record(record_id=2,
                                                                       employee_id=1002)])

        result = flag_roster_breach(records, roster(), tolerance_minutes=30)

        assert result["record_id"].tolist() == [1, 2]


# ------------------------------------------------------------------- combined / edges

class TestApplyExceptionRules:
    def test_empty_input_returns_empty_frame_with_columns(self, records_frame, roster):
        result = apply_exception_rules(records_frame([]), roster(), 10, 30)

        assert result.empty
        assert list(result.columns) == EXCEPTION_COLUMNS

    def test_clean_record_raises_nothing(self, make_record, records_frame, day_roster):
        records = records_frame([make_record(clock_in=f"{D} 06:03", clock_out=f"{D} 14:04")])

        assert apply_exception_rules(records, day_roster, 10, 30).empty

    def test_overtime_shift_is_also_roster_breach(self, make_record, records_frame,
                                                  day_roster):
        records = records_frame([make_record(clock_out=f"{D} 17:00")])  # 11 h, out +180

        result = apply_exception_rules(records, day_roster, 10, 30)

        assert sorted(result["type"]) == [OVERTIME, ROSTER_BREACH]

    def test_one_exception_per_record_and_type(self, make_record, records_frame, roster):
        records = records_frame([
            make_record(record_id=1, clock_out=None),
            make_record(record_id=2, employee_id=1002, clock_in=f"{D} 07:00",
                        clock_out=f"{D} 18:00"),
        ])
        rosters = roster((1001, D, f"{D} 06:00", f"{D} 14:00"),
                         (1002, D, f"{D} 06:00", f"{D} 14:00"))

        result = apply_exception_rules(records, rosters, 10, 30)

        assert not result.duplicated(subset=["record_id", "type"]).any()
        assert set(zip(result["record_id"], result["type"])) == {
            (1, MISSED_CLOCK_IN), (2, OVERTIME), (2, ROSTER_BREACH),
        }

    def test_output_columns(self, make_record, records_frame, day_roster):
        records = records_frame([make_record(clock_out=None)])

        result = apply_exception_rules(records, day_roster, 10, 30)

        assert list(result.columns) == EXCEPTION_COLUMNS
