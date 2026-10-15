# SecureLock Global - Time and Attendance Reporting System

This system ingests attendance data, flags exceptions automatically (missed clock-outs,
overtime, and roster breaches), and delivers scheduled reports through a Metabase
dashboard.

```bash
docker compose up -d
```

Then open <http://localhost:3000>.

- Setup, configuration and operations: **[docs/README.md](docs/README.md)**
- Dashboard, weekly email and alert: **[docs/metabase_setup.md](docs/metabase_setup.md)**
