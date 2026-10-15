-- =====================================================================
-- Metabase native (SQL) questions for the Attendance Overview dashboard.
-- Paste each query into: + New > SQL query, database "SecureLock Attendance".
-- See docs/metabase_setup.md for visualization settings and the dashboard layout.
-- =====================================================================


-- 1. Total Staff  (visualization: Number)
SELECT COUNT(*) AS total_staff
FROM public.employees;


-- 2. Present Today  (visualization: Number)
SELECT COUNT(DISTINCT employee_id) AS present_today
FROM reporting.v_present_today;


-- 3. Flagged Exceptions - Open  (visualization: Number)
SELECT COUNT(*) AS open_exceptions
FROM public.exceptions
WHERE status = 'open';


-- 3b. Open Exceptions by Type  (optional, visualization: Row or Pie)
SELECT type, COUNT(*) AS open_exceptions
FROM public.exceptions
WHERE status = 'open'
GROUP BY type
ORDER BY open_exceptions DESC;


-- 4. Hours by Department, last 7 days  (visualization: Bar; X = department, Y = total_hours)
SELECT department, SUM(total_hours) AS total_hours
FROM reporting.v_hours_by_department
WHERE date >= CURRENT_DATE - 6
GROUP BY department
ORDER BY total_hours DESC;


-- 4b. Hours by Department with a dashboard date filter  (optional)
-- After pasting, open the variables sidebar and set {{date_range}} to:
--   Variable type: Field Filter
--   Field to map to: V Hours By Department -> Date
--   Filter widget type: Date Filter
SELECT department, SUM(total_hours) AS total_hours
FROM reporting.v_hours_by_department
WHERE {{date_range}}
GROUP BY department
ORDER BY total_hours DESC;


-- 5. Recent Exceptions, last 14 days  (visualization: Table)
SELECT date, employee_name, department, type, status, details,
       clock_in, clock_out, scheduled_start, scheduled_end
FROM reporting.v_recent_exceptions
ORDER BY date DESC, exception_id DESC
LIMIT 200;


-- 6. Open Exceptions - alert source  (visualization: Table)
-- Used by the "open exceptions" alert: Metabase sends it whenever it returns rows.
SELECT exception_id, date, employee_name, department, manager_name, type, details
FROM reporting.v_open_exceptions
ORDER BY date DESC, exception_id DESC;
