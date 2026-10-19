"""Local SQLite history of dictations. Never allowed to break the pipeline."""

import sqlite3
import time

from .config import CONFIG_DIR

DB_PATH = CONFIG_DIR / "history.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    app_name TEXT,
    bundle_id TEXT,
    context TEXT,
    raw_text TEXT,
    final_text TEXT,
    cleaned INTEGER,
    audio_seconds REAL,
    model TEXT,
    engine TEXT,
    transcribe_seconds REAL,
    cleanup_seconds REAL
)
"""

# columns added after the first release; applied to pre-existing DBs
_MIGRATIONS = (
    "ALTER TABLE history ADD COLUMN engine TEXT",
    "ALTER TABLE history ADD COLUMN transcribe_seconds REAL",
    "ALTER TABLE history ADD COLUMN cleanup_seconds REAL",
)


def _connect() -> sqlite3.Connection:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH)
    connection.execute(_SCHEMA)
    for statement in _MIGRATIONS:
        try:
            connection.execute(statement)
        except sqlite3.OperationalError:
            pass  # column already exists
    return connection


_QUERY_COLUMNS = (
    "id", "ts", "app_name", "context", "raw_text", "final_text",
    "cleaned", "audio_seconds", "engine", "transcribe_seconds",
    "cleanup_seconds",
)


def query(search: str = "", limit: int = 50) -> list[dict]:
    """Most recent dictations, optionally filtered by substring."""
    connection = _connect()
    try:
        sql = f"SELECT {', '.join(_QUERY_COLUMNS)} FROM history"
        params: list = []
        if search:
            sql += " WHERE final_text LIKE ? OR raw_text LIKE ? OR app_name LIKE ?"
            params = [f"%{search}%"] * 3
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(max(1, min(int(limit), 500)))
        rows = connection.execute(sql, params).fetchall()
        return [dict(zip(_QUERY_COLUMNS, row)) for row in rows]
    finally:
        connection.close()


def record(
    app_name: str,
    bundle_id: str,
    context: str,
    raw_text: str,
    final_text: str,
    cleaned: bool,
    audio_seconds: float,
    model: str,
    engine: str = "",
    transcribe_seconds: float = 0.0,
    cleanup_seconds: float = 0.0,
) -> None:
    connection = _connect()
    try:
        connection.execute(
            "INSERT INTO history (ts, app_name, bundle_id, context, raw_text,"
            " final_text, cleaned, audio_seconds, model, engine,"
            " transcribe_seconds, cleanup_seconds)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                time.strftime("%Y-%m-%d %H:%M:%S"),
                app_name,
                bundle_id,
                context,
                raw_text,
                final_text,
                int(cleaned),
                round(audio_seconds, 2),
                model,
                engine,
                round(transcribe_seconds, 2),
                round(cleanup_seconds, 2),
            ),
        )
        connection.commit()
    finally:
        connection.close()
