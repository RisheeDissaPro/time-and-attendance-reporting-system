-- =====================================================================
-- Reporting views consumed by Metabase, plus read-only grants.
-- Runs after schema.sql on the first start of the postgres container.
-- =====================================================================

BEGIN;

CREATE SCHEMA IF NOT EXISTS reporting;

-- One row per employee with department context.
CREATE VIEW reporting.v_employee_directory AS
SELECT
    e.employee_id,
    e.name           AS employee_name,
    e.role,
    d.department_id,
    d.name           AS department,
    d.business_unit,
    m.name           AS manager_name
FROM employees e
JOIN departments d ON d.department_id = e.department_id
LEFT JOIN employees m ON m.employee_id = e.manager_id;

-- Everyone with an attendance record dated today (server timezone).
CREATE VIEW reporting.v_present_today AS
SELECT
    a.record_id,
    a.employee_id,
    ed.employee_name,
    ed.department,
    a.clock_in,
    a.clock_out,
    a.hours_worked
FROM attendance_records a
JOIN reporting.v_employee_directory ed ON ed.employee_id = a.employee_id
WHERE a.date = CURRENT_DATE;

-- Hours worked per department per day.
CREATE VIEW reporting.v_hours_by_department AS
SELECT
    a.date,
    ed.department,
    ed.business_unit,
    COUNT(DISTINCT a.employee_id)          AS employees_present,
    COALESCE(SUM(a.hours_worked), 0)::NUMERIC(10, 2) AS total_hours
FROM attendance_records a
JOIN reporting.v_employee_directory ed ON ed.employee_id = a.employee_id
GROUP BY a.date, ed.department, ed.business_unit;

-- All exceptions with employee, department and shift context.
CREATE VIEW reporting.v_exceptions AS
SELECT
    x.exception_id,
    x.date,
    x.type,
    x.status,
    x.details,
    x.employee_id,
    ed.employee_name,
    ed.department,
    ed.manager_name,
    a.clock_in,
    a.clock_out,
    a.hours_worked,
    r.scheduled_start,
    r.scheduled_end,
    x.created_at,
    x.updated_at
FROM exceptions x
JOIN attendance_records a ON a.record_id = x.record_id
JOIN reporting.v_employee_directory ed ON ed.employee_id = x.employee_id
LEFT JOIN rosters r ON r.employee_id = a.employee_id AND r.shift_date = a.date;

-- Exceptions from the last 14 days.
CREATE VIEW reporting.v_recent_exceptions AS
SELECT *
FROM reporting.v_exceptions
WHERE date >= CURRENT_DATE - 13;

-- Exceptions still awaiting action.
CREATE VIEW reporting.v_open_exceptions AS
SELECT *
FROM reporting.v_exceptions
WHERE status = 'open';

-- Metabase gets read-only access to reference, cleaned and reporting data.
-- The staging (raw) schema is intentionally not exposed.
GRANT USAGE ON SCHEMA public, reporting TO metabase_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA public, reporting TO metabase_reader;
ALTER DEFAULT PRIVILEGES IN SCHEMA public, reporting
    GRANT SELECT ON TABLES TO metabase_reader;

COMMIT;
