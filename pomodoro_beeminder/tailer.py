import asyncio
import re
import shlex
from pathlib import Path
from typing import Literal

import aiorun
import fire
from monitored_subprocess import MonitoredSubprocess
from pysqlitedb import DB, Column, Table
from snoop import pp

OBJECT_PATH = "/timepp/zagortenay333/Pomodoro"

PomoState = Literal["POMO", "STOPPED", "LONG_BREAK", "SHORT_BREAK"]
STATE_CHANGED_RE = re.compile(
    f"^{OBJECT_PATH}:\s+.*?\.pomo_state_changed\s+\('(POMO|STOPPED|LONG_BREAK|SHORT_BREAK)'\,\)$"
)


async def start_gdbus() -> MonitoredSubprocess:
    proc = MonitoredSubprocess(
        "gdbus",
        await asyncio.create_subprocess_exec(
            "gdbus",
            *shlex.split(
                f"monitor --session --dest org.gnome.Shell --object-path {OBJECT_PATH}"
            ),
            stdout=asyncio.subprocess.PIPE,
        ),
    )
    return proc


async def tail_gdbus(proc: asyncio.subprocess.Process, db: DB):
    proc_out = proc.stdout
    assert proc_out is not None
    while True:
        line = (await proc_out.readline()).decode("utf-8").strip()
        if match := STATE_CHANGED_RE.match(line):
            state = match.group(1)
            db.insert_row(
                tablename="pomo_state_changes",
                values={"state": state, "timestamp": db.utcnow()},
            )


def get_db(db_path: Path):
    tables = [
        Table(
            "pomo_state_changes",
            columns=[
                Column("state", "TEXT NOT NULL"),
                Column("timestamp", "TEXT NOT NULL"),
                Column("created_at", "TEXT NOT NULL"),
            ],
        )
    ]
    return DB.get(db_path, tables=tables)


async def mainloop(db_path: Path) -> None:
    try:
        gdbus_proc = await start_gdbus()
        with get_db(db_path) as db:
            await asyncio.gather(tail_gdbus(gdbus_proc.proc, db))
    except asyncio.CancelledError:
        await gdbus_proc.stop()


def syncmain(output_file: str):
    aiorun.run(mainloop(Path(output_file)), stop_on_unhandled_errors=True)


def main():
    fire.Fire(syncmain)


if __name__ == "__main__":
    fire.Fire(syncmain)
