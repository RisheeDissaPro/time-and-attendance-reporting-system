"""Generate realistic sample data for the SecureLock Global attendance system.

Writes four CSV files to the output directory:

* ``departments.csv``       - 5 departments
* ``employees.csv``         - ~50 employees (one supervisor per department)
* ``rosters.csv``           - 4 weeks of scheduled shifts
* ``attendance_export.csv`` - a deliberately messy timeclock export

The attendance export contains injected anomalies so that every exception
rule and every cleaning step in the ETL has something to catch: missing
clock-outs, shifts over 10 hours, clock-ins more than 30 minutes off roster,
duplicate rows, mixed date formats, padded values, blank and unknown
employee IDs, and absences.

Usage::

    python -m db.generate_sample_data [--start-date YYYY-MM-DD] [--days 28]
                                      [--seed 42] [--out-dir data]
"""

from __future__ import annotations

import argparse
import csv
import logging
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

DEPARTMENTS: list[tuple[int, str, str, tuple[str, ...]]] = [
    (1, "Assembly", "Lock Manufacturing", ("Assembly Technician", "Line Operator")),
    (2, "Machining", "Lock Manufacturing", ("CNC Machinist", "Machine Operator")),
    (3, "Quality Control", "Quality Assurance", ("Quality Inspector", "QA Technician")),
    (4, "Logistics", "Supply Chain", ("Warehouse Associate", "Forklift Operator")),
    (5, "Maintenance", "Engineering Services", ("Maintenance Technician", "Electrician")),
]
EMPLOYEES_PER_DEPARTMENT = 10
FIRST_EMPLOYEE_ID = 1001

FIRST_NAMES = (
    "Aarav", "Priya", "James", "Maria", "Wei", "Fatima", "Lucas", "Aisha",
    "Daniel", "Sofia", "Rahul", "Emma", "Kenji", "Olivia", "Mateo", "Chloe",
    "Arjun", "Grace", "Noah", "Ananya", "Liam", "Zara", "Ethan", "Mei",
    "Omar", "Isabella", "Vikram", "Hannah", "Diego", "Leila",
)
LAST_NAMES = (
    "Sharma", "Smith", "Chen", "Garcia", "Patel", "Khan", "Silva", "Nguyen",
    "Brown", "Rossi", "Iyer", "Müller", "Tanaka", "Wilson", "Lopez", "Ahmed",
    "Kumar", "Taylor", "Park", "Okafor", "Fernandes", "Novak", "Haddad", "Reddy",
)

SHIFT_PATTERNS: dict[str, tuple[time, time]] = {
    "morning": (time(6, 0), time(14, 0)),
    "afternoon": (time(14, 0), time(22, 0)),
    "night": (time(22, 0), time(6, 0)),
}
SHIFT_WEIGHTS = {"morning": 0.5, "afternoon": 0.3, "night": 0.2}
SATURDAY_WORKER_SHARE = 0.3

ABSENCE_RATE = 0.02
MISSING_CLOCK_OUT_RATE = 0.03
OVERTIME_RATE = 0.04
OFF_ROSTER_CLOCK_IN_RATE = 0.05
DUPLICATE_ROW_RATE = 0.01
ALT_DATE_FORMAT_RATE = 0.05
PADDED_VALUE_RATE = 0.03
SECONDS_TIME_FORMAT_RATE = 0.10
UNKNOWN_EMPLOYEE_IDS = (9998, 9999)
BLANK_EMPLOYEE_ROWS = 2

ISO_DATE_FORMAT = "%Y-%m-%d"
ALT_DATE_FORMAT = "%d/%m/%Y"


@dataclass(frozen=True)
class Employee:
    """An employee row as written to ``employees.csv``."""

    employee_id: int
    name: str
    department_id: int
    role: str
    manager_id: int | None
    shift: str
    works_saturday: bool


@dataclass(frozen=True)
class RosterShift:
    """A scheduled shift as written to ``rosters.csv``."""

    employee_id: int
    shift_date: date
    scheduled_start: datetime
    scheduled_end: datetime


def build_employees(rng: np.random.Generator) -> list[Employee]:
    """Create one supervisor plus staff for each department.

    Args:
        rng: Seeded random generator.

    Returns:
        Employees ordered so that every supervisor precedes their reports.
    """
    names = [f"{first} {last}" for first in FIRST_NAMES for last in LAST_NAMES]
    chosen = rng.choice(len(names), size=len(DEPARTMENTS) * EMPLOYEES_PER_DEPARTMENT,
                        replace=False)
    name_iter = iter(names[i] for i in chosen)
    shift_names = list(SHIFT_WEIGHTS)
    shift_probs = list(SHIFT_WEIGHTS.values())

    employees: list[Employee] = []
    next_id = FIRST_EMPLOYEE_ID
    for department_id, department_name, _, roles in DEPARTMENTS:
        supervisor_id = next_id
        employees.append(Employee(
            employee_id=supervisor_id,
            name=next(name_iter),
            department_id=department_id,
            role=f"{department_name} Supervisor",
            manager_id=None,
            shift="morning",
            works_saturday=False,
        ))
        next_id += 1
        for _ in range(EMPLOYEES_PER_DEPARTMENT - 1):
            employees.append(Employee(
                employee_id=next_id,
                name=next(name_iter),
                department_id=department_id,
                role=str(rng.choice(roles)),
                manager_id=supervisor_id,
                shift=str(rng.choice(shift_names, p=shift_probs)),
                works_saturday=bool(rng.random() < SATURDAY_WORKER_SHARE),
            ))
            next_id += 1
    return employees


def build_rosters(employees: list[Employee], start: date, days: int) -> list[RosterShift]:
    """Schedule weekday shifts (plus Saturdays for some staff) for each employee.

    Args:
        employees: Employees to roster.
        start: First roster date.
        days: Number of consecutive days to cover.

    Returns:
        Roster shifts ordered by date, then employee.
    """
    shifts: list[RosterShift] = []
    for offset in range(days):
        shift_date = start + timedelta(days=offset)
        weekday = shift_date.weekday()
        for employee in employees:
            if weekday == 6 or (weekday == 5 and not employee.works_saturday):
                continue
            start_time, end_time = SHIFT_PATTERNS[employee.shift]
            scheduled_start = datetime.combine(shift_date, start_time)
            scheduled_end = datetime.combine(shift_date, end_time)
            if scheduled_end <= scheduled_start:
                scheduled_end += timedelta(days=1)
            shifts.append(RosterShift(employee.employee_id, shift_date,
                                      scheduled_start, scheduled_end))
    return shifts


def _minutes(value: float) -> timedelta:
    """Return a ``timedelta`` of ``value`` whole minutes."""
    return timedelta(minutes=int(round(value)))


def _format_time(moment: datetime, rng: np.random.Generator) -> str:
    """Format a punch time the way the timeclock does (mostly ``HH:MM``)."""
    if rng.random() < SECONDS_TIME_FORMAT_RATE:
        return moment.strftime("%H:%M:%S")
    return moment.strftime("%H:%M")


def build_attendance(
    rosters: list[RosterShift], rng: np.random.Generator
) -> tuple[list[dict[str, str]], Counter[str]]:
    """Simulate a timeclock export for the rostered shifts.

    Punches are exported as a date plus clock-in/clock-out times of day. A
    clock-out earlier than the clock-in means the shift ran past midnight.

    Args:
        rosters: Scheduled shifts to simulate.
        rng: Seeded random generator.

    Returns:
        The export rows (all values as strings) and a count of injected anomalies.
    """
    rows: list[dict[str, str]] = []
    injected: Counter[str] = Counter()

    for shift in rosters:
        if rng.random() < ABSENCE_RATE:
            injected["absence (no row)"] += 1
            continue

        clock_in = shift.scheduled_start + _minutes(rng.uniform(-10, 10))
        clock_out: datetime | None = shift.scheduled_end + _minutes(rng.uniform(-5, 10))

        roll = rng.random()
        if roll < MISSING_CLOCK_OUT_RATE:
            clock_out = None
            injected["missing clock-out"] += 1
        elif roll < MISSING_CLOCK_OUT_RATE + OVERTIME_RATE:
            clock_out = shift.scheduled_end + _minutes(rng.uniform(150, 240))
            injected["shift over 10 hours"] += 1
        elif roll < MISSING_CLOCK_OUT_RATE + OVERTIME_RATE + OFF_ROSTER_CLOCK_IN_RATE:
            sign = 1 if rng.random() < 0.75 else -1
            # Capped below 2h so a late night-shift punch never crosses midnight,
            # which a date + time-of-day export cannot represent.
            clock_in = shift.scheduled_start + sign * _minutes(rng.uniform(35, 110))
            injected["clock-in >30 min off roster"] += 1

        date_format = ISO_DATE_FORMAT
        if rng.random() < ALT_DATE_FORMAT_RATE:
            date_format = ALT_DATE_FORMAT
            injected["alternate date format"] += 1

        employee_id = str(shift.employee_id)
        if rng.random() < PADDED_VALUE_RATE:
            employee_id = f" {employee_id} "
            injected["whitespace-padded value"] += 1

        rows.append({
            "employee_id": employee_id,
            "date": shift.shift_date.strftime(date_format),
            "clock_in": _format_time(clock_in, rng),
            "clock_out": _format_time(clock_out, rng) if clock_out else "",
        })

    duplicate_count = max(1, int(len(rows) * DUPLICATE_ROW_RATE))
    for index in sorted(rng.choice(len(rows), size=duplicate_count, replace=False),
                        reverse=True):
        rows.insert(int(index) + 1, dict(rows[int(index)]))
    injected["exact duplicate row"] += duplicate_count

    sample_day = rosters[len(rosters) // 2].shift_date.strftime(ISO_DATE_FORMAT)
    for unknown_id in UNKNOWN_EMPLOYEE_IDS:
        rows.append({"employee_id": str(unknown_id), "date": sample_day,
                     "clock_in": "06:02", "clock_out": "14:01"})
        injected["unknown employee id"] += 1
    for _ in range(BLANK_EMPLOYEE_ROWS):
        rows.append({"employee_id": "", "date": sample_day,
                     "clock_in": "14:05", "clock_out": "22:00"})
        injected["blank employee id"] += 1

    return rows, injected


def _write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, object]]) -> None:
    """Write ``rows`` to ``path`` as UTF-8 CSV with a header."""
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    logger.info("Wrote %d rows to %s", len(rows), path)


def generate(out_dir: Path, start: date, days: int, seed: int) -> Counter[str]:
    """Generate all sample CSV files.

    Args:
        out_dir: Directory to write the CSV files to (created if missing).
        start: First date covered by rosters and attendance.
        days: Number of days to cover.
        seed: Random seed, for reproducible output.

    Returns:
        Count of injected anomalies by kind.
    """
    rng = np.random.default_rng(seed)
    out_dir.mkdir(parents=True, exist_ok=True)

    employees = build_employees(rng)
    rosters = build_rosters(employees, start, days)
    attendance, injected = build_attendance(rosters, rng)

    _write_csv(out_dir / "departments.csv", ["department_id", "name", "business_unit"],
               [{"department_id": d_id, "name": name, "business_unit": unit}
                for d_id, name, unit, _ in DEPARTMENTS])
    _write_csv(out_dir / "employees.csv",
               ["employee_id", "name", "department_id", "role", "manager_id"],
               [{"employee_id": e.employee_id, "name": e.name,
                 "department_id": e.department_id, "role": e.role,
                 "manager_id": e.manager_id if e.manager_id is not None else ""}
                for e in employees])
    _write_csv(out_dir / "rosters.csv",
               ["employee_id", "shift_date", "scheduled_start", "scheduled_end"],
               [{"employee_id": r.employee_id, "shift_date": r.shift_date.isoformat(),
                 "scheduled_start": r.scheduled_start.isoformat(sep=" "),
                 "scheduled_end": r.scheduled_end.isoformat(sep=" ")}
                for r in rosters])
    _write_csv(out_dir / "attendance_export.csv",
               ["employee_id", "date", "clock_in", "clock_out"], attendance)

    logger.info("Covered %s to %s: %d employees, %d rostered shifts",
                start, start + timedelta(days=days - 1), len(employees), len(rosters))
    for kind, count in sorted(injected.items()):
        logger.info("Injected %-30s %d", kind, count)
    return injected


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--days", type=int, default=28,
                        help="number of days to generate (default: 28)")
    parser.add_argument("--start-date", type=date.fromisoformat, default=None,
                        help="first date, YYYY-MM-DD (default: so the range ends today)")
    parser.add_argument("--seed", type=int, default=42, help="random seed (default: 42)")
    parser.add_argument("--out-dir", type=Path, default=Path("data"),
                        help="output directory (default: data)")
    args = parser.parse_args(argv)
    if args.days < 1:
        parser.error("--days must be at least 1")
    if args.start_date is None:
        args.start_date = date.today() - timedelta(days=args.days - 1)
    return args


def main(argv: list[str] | None = None) -> int:
    """Command-line entry point."""
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    args = parse_args(argv)
    generate(args.out_dir, args.start_date, args.days, args.seed)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
