"""Runtime configuration loaded from environment variables (and an optional ``.env``)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy.engine import URL

DEFAULT_OVERTIME_THRESHOLD_HOURS = 10.0
DEFAULT_ROSTER_TOLERANCE_MINUTES = 30.0
VALID_LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")


class ConfigError(ValueError):
    """Raised when a configuration value is missing or invalid."""


@dataclass(frozen=True)
class Settings:
    """Validated settings shared by the ETL and the seed script."""

    database_url: str | URL
    data_dir: Path
    attendance_csv_path: Path
    overtime_threshold_hours: float = DEFAULT_OVERTIME_THRESHOLD_HOURS
    roster_tolerance_minutes: float = DEFAULT_ROSTER_TOLERANCE_MINUTES
    log_level: str = "INFO"
    db_connect_retries: int = 10
    db_connect_delay_seconds: float = 3.0

    def __post_init__(self) -> None:
        """Reject values that would make the exception rules meaningless."""
        if self.overtime_threshold_hours <= 0:
            raise ConfigError("OVERTIME_THRESHOLD_HOURS must be greater than 0")
        if self.roster_tolerance_minutes < 0:
            raise ConfigError("ROSTER_TOLERANCE_MINUTES must be 0 or greater")
        if self.log_level not in VALID_LOG_LEVELS:
            raise ConfigError(f"LOG_LEVEL must be one of {', '.join(VALID_LOG_LEVELS)}")
        if self.db_connect_retries < 1:
            raise ConfigError("DB_CONNECT_RETRIES must be at least 1")


def _get_float(name: str, default: float) -> float:
    """Read an environment variable as a float, with a clear error on bad input."""
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be a number, got {raw!r}") from exc


def _get_int(name: str, default: int) -> int:
    """Read an environment variable as an int, with a clear error on bad input."""
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer, got {raw!r}") from exc


def _database_url() -> str | URL:
    """Use ``DATABASE_URL`` if set, otherwise build one from ``POSTGRES_*`` variables."""
    explicit = os.getenv("DATABASE_URL", "").strip()
    if explicit:
        return explicit
    password = os.getenv("POSTGRES_PASSWORD")
    if not password:
        raise ConfigError("Set DATABASE_URL or POSTGRES_PASSWORD (see .env.example)")
    return URL.create(
        drivername="postgresql+psycopg2",
        username=os.getenv("POSTGRES_USER", "attendance_admin"),
        password=password,
        host=os.getenv("POSTGRES_HOST", "localhost"),
        port=_get_int("POSTGRES_PORT", 5432),
        database=os.getenv("POSTGRES_DB", "attendance"),
    )


def load_settings(env_file: str | Path | None = ".env") -> Settings:
    """Load settings from the environment.

    Variables already set in the environment take precedence over ``env_file``.

    Args:
        env_file: Optional dotenv file to read; ``None`` skips it.

    Returns:
        Validated settings.

    Raises:
        ConfigError: If a value is missing or invalid.
    """
    if env_file is not None:
        load_dotenv(env_file, override=False)

    data_dir = Path(os.getenv("DATA_DIR", "data"))
    csv_path = os.getenv("ATTENDANCE_CSV_PATH", "").strip()
    return Settings(
        database_url=_database_url(),
        data_dir=data_dir,
        attendance_csv_path=Path(csv_path) if csv_path else data_dir / "attendance_export.csv",
        overtime_threshold_hours=_get_float("OVERTIME_THRESHOLD_HOURS",
                                            DEFAULT_OVERTIME_THRESHOLD_HOURS),
        roster_tolerance_minutes=_get_float("ROSTER_TOLERANCE_MINUTES",
                                            DEFAULT_ROSTER_TOLERANCE_MINUTES),
        log_level=os.getenv("LOG_LEVEL", "INFO").strip().upper(),
        db_connect_retries=_get_int("DB_CONNECT_RETRIES", 10),
        db_connect_delay_seconds=_get_float("DB_CONNECT_DELAY_SECONDS", 3.0),
    )
