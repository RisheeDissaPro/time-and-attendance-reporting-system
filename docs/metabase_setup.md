# Metabase setup: dashboard, weekly email report and exception alert

These steps were written against Metabase **v0.64** (the version pinned in
`docker-compose.yml`). Menu labels can move slightly between releases, but the
flow is the same.

All SQL referenced below lives in [`metabase_queries.sql`](metabase_queries.sql).

---

## 1. First-run setup

1. Start the stack: `docker compose up -d` (add `--profile mail` to also start
   the local mailpit SMTP sink used for testing emails).
2. Wait about a minute, then open <http://localhost:3000>. (Metabase is ready when
   <http://localhost:3000/api/health> returns `{"status":"ok"}`.)
3. Click **Let's get started**, choose a language, and create the admin account.
4. At **Add your data**, choose **PostgreSQL** and enter:

   | Field         | Value                                              |
   |---------------|----------------------------------------------------|
   | Display name  | `SecureLock Attendance`                            |
   | Host          | `postgres`  (the compose service name)             |
   | Port          | `5432`                                             |
   | Database name | `attendance` (or your `POSTGRES_DB`)               |
   | Username      | `metabase_reader`                                  |
   | Password      | value of `METABASE_READER_PASSWORD` (default `change_me_reader`) |
   | Schemas       | **Only these…** → `public`, `reporting`            |

   `metabase_reader` is read-only and cannot see the raw `staging` schema.
5. Finish the wizard. Metabase syncs the schema in the background, which takes a few seconds.

Metabase stores its own settings, questions and dashboards in the separate
`metabase` Postgres database, so they survive container restarts.

**Timezone:** under **Admin settings → Settings → Localization**, set
**Report timezone** to the plant's timezone (the same value as `TZ` in `.env`),
so that "today" matches the ETL and the database.

---

## 2. Create the questions

For each query below, do the following:

1. Click **+ New → SQL query**.
2. Pick the **SecureLock Attendance** database.
3. Paste the SQL and click **Run**.
4. Choose the visualization.
5. Click **Save**, giving the name shown in the table, and save it into a new
   collection called **Attendance Reporting**.

| # | Question name                    | SQL block in `metabase_queries.sql` | Visualization |
|---|----------------------------------|-------------------------------------|---------------|
| 1 | Total Staff                      | 1                                   | Number        |
| 2 | Present Today                    | 2                                   | Number        |
| 3 | Flagged Exceptions (Open)        | 3                                   | Number        |
| 4 | Hours by Department (Last 7 Days)| 4 (or 4b for a date filter)         | Bar: X `department`, Y `total_hours` |
| 5 | Recent Exceptions                | 5                                   | Table         |
| 6 | Open Exceptions                  | 6                                   | Table (used by the alert) |

Optional: **Open Exceptions by Type** (block 3b) works well as a Row or Pie chart.

**Optional date filter (block 4b):** after pasting, Metabase detects
`{{date_range}}` and opens the variables sidebar. Set **Variable type** to
**Field Filter**, map it to **V Hours By Department → Date**, and set the
**Filter widget type** to **Date filter**.

---

## 3. Build the "Attendance Overview" dashboard

1. Click **+ New → Dashboard**, name it **Attendance Overview**, and save it in **Attendance Reporting**.
2. Click the **pencil (Edit)** icon, then **Add a chart** (the **+** icon), and add the questions:
   - Top row: **Total Staff**, **Present Today**, **Flagged Exceptions (Open)**
     (the three Number cards side by side).
   - Middle row: **Hours by Department (Last 7 Days)**, at full width.
   - Bottom row: **Recent Exceptions**, at full width.
3. *(Optional, only if you used block 4b)* Click **Add a filter → Date picker → All options**,
   label it **Date range**, connect it to the `date_range` variable on the
   Hours by Department card, and set a default of **Previous 7 days**.
4. Click **Save**.

The ETL runs nightly at 02:00, so the dashboard shows the previous day's
data when people arrive in the morning. Metabase caches query results
only if you turn caching on, so the cards always reflect the latest load.

---

## 4. Email (SMTP)

Emailed reports and alerts need SMTP. The compose file passes these
`MB_EMAIL_*` variables from `.env` into Metabase:

| Variable                 | Default (mailpit)            |
|--------------------------|------------------------------|
| `MB_EMAIL_SMTP_HOST`     | `mailpit`                    |
| `MB_EMAIL_SMTP_PORT`     | `1025`                       |
| `MB_EMAIL_SMTP_SECURITY` | `none` (`tls`, `ssl`, `starttls` also accepted) |
| `MB_EMAIL_SMTP_USERNAME` | *(empty)*                    |
| `MB_EMAIL_SMTP_PASSWORD` | *(empty)*                    |
| `MB_EMAIL_FROM_ADDRESS`  | `reports@securelock.example` |

- **Local testing:** run `docker compose --profile mail up -d`. Every email Metabase
  sends then appears at <http://localhost:8025>.
- **Production:** set the variables to your real SMTP relay in `.env`, then run
  `docker compose up -d metabase` to apply them.

Settings supplied through environment variables show as read-only under
**Admin settings → Settings → Email** (or **Notification channels → Email** in
newer releases). Use **Send test email** there to confirm delivery.

---

## 5. Weekly emailed report (dashboard subscription)

1. Open the **Attendance Overview** dashboard.
2. Click the **Sharing** icon in the dashboard header, then **Subscriptions** (in some
   versions it's labelled **Create a dashboard subscription**).
3. Choose **Email** and add the recipients (for example plant managers and HR).
4. Set **Frequency** to **Weekly**, the day to **Monday**, and the time to **8:00 AM**.
5. *(Optional, only if you added the date filter)* Under **Set filter values for when this
   subscription is sent**, set **Date range** to **Previous 7 days**.
6. Optionally turn on **Attach results** and pick **.xlsx** or **.csv**, so recipients also
   get the raw tables.
7. Click **Done**. Use **Send email now** to test it; with mailpit, check <http://localhost:8025>.

---

## 6. Alert for open exceptions

1. Open the saved **Open Exceptions** question.
2. Click the **bell (Create an alert)** icon. In some versions it's under the
   **...** menu, as **Create an alert**.
3. Set **What do you want to be alerted about?** to **When this question has results**.
4. Set **When do you want to check?** to **Daily** at **8:00 AM**. Because the ETL runs
   at 02:00, every check sees fresh data.
5. Choose **Email** and add the supervisors or HR distribution list.
6. Leave **Only send this alert once** off, so the alert repeats daily until every
   exception is resolved or escalated.
7. Click **Done**.

**To get alerted only about new problems:** save a variant of the question that adds
`AND created_at >= now() - interval '1 day'`, and attach the alert to that variant
instead.

---

## 7. Working the exception queue

Metabase connects read-only, so supervisors update the status of exceptions
directly in Postgres. Alternatively, you can grant `UPDATE (status)` on
`exceptions` to a dedicated role and use Metabase Actions. For example:

```sql
UPDATE exceptions SET status = 'resolved' WHERE exception_id = 123;
UPDATE exceptions SET status = 'escalated' WHERE exception_id = 456;
```

The ETL never overwrites an existing exception, so these statuses persist
across nightly runs. `updated_at` is maintained automatically.
