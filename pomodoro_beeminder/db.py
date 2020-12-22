from pathlib import Path

from pysqlitedb import DB, Column, Table


def get_db(db_path: Path):
    tables = [
        Table(
            "pomo_state_changes",
            columns=[
                Column("state", "TEXT NOT NULL"),
                Column("timestamp", "TEXT NOT NULL"),
                Column("created_at", "TEXT NOT NULL"),
            ],
        ),
        Table(
            "beeminder_posts",
            columns=[
                Column("posted_at", "TEXT NOT NULL"),
                Column("error", "TEXT"),
                Column("created_at", "TEXT NOT NULL"),
            ],
        ),
    ]
    return DB.get(db_path, tables=tables)


def get_default_db_path() -> Path:
    local_dir = Path.home() / ".local" / "share" / "pomodoro_beeminder"
    local_dir.mkdir(exist_ok=True, parents=True)

    return local_dir / "data.sqlite"
