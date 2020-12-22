import asyncio
import re
import shlex
import subprocess
from pathlib import Path
from typing import Literal, Optional

import aiorun
import backoff
import fire
from monitored_subprocess import MonitoredSubprocess
from pysqlitedb import DB
from rich import print

from .db import get_db, get_default_db_path

OBJECT_PATH = "/timepp/zagortenay333/Pomodoro"

PomoState = Literal["POMO", "STOPPED", "LONG_BREAK", "SHORT_BREAK"]
STATE_CHANGED_RE = re.compile(
    f"^{OBJECT_PATH}:\s+.*?\.pomo_state_changed\s+\('(POMO|STOPPED|LONG_BREAK|SHORT_BREAK)'\,\)$"
)


@backoff.on_exception(backoff.expo, subprocess.CalledProcessError)
def get_gnome_shell_pid() -> str:
    try:
        proc = subprocess.run(
            ["pidof", "-s", "gnome-shell"], check=True, capture_output=True
        )
    except subprocess.CalledProcessError as e:
        print(f"[red]pidof gnome-shell raised (will retry):[/red] {e}")
        raise
    return proc.stdout.decode("utf-8").strip()


def get_dbus_address() -> str:
    pid = get_gnome_shell_pid()
    proc = subprocess.run(
        f"strings /proc/{pid}/environ | grep DBUS_SESSION_BUS_ADDRESS",
        check=True,
        capture_output=True,
        shell=True,
    )
    dbus_address_var = proc.stdout.decode("utf-8").strip()
    return dbus_address_var[len("DBUS_SESSION_BUS_ADDRESS=") :]


async def start_gdbus() -> MonitoredSubprocess:
    dbus_address = get_dbus_address()
    gdbus_args = f"monitor --dest org.gnome.Shell --object-path {OBJECT_PATH} --address {dbus_address}"
    print(f"Starting gdbus with args [green]{gdbus_args}[/green]")
    proc = MonitoredSubprocess(
        "gdbus",
        await asyncio.create_subprocess_exec(
            "gdbus",
            *shlex.split(gdbus_args),
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
            print(f"gdbus event [green]{state}[/green]")


async def mainloop(db_path: Path) -> None:
    while True:
        try:
            gdbus_proc = await start_gdbus()
            with get_db(db_path) as db:
                await asyncio.gather(tail_gdbus(gdbus_proc.proc, db), gdbus_proc.wait())
        except asyncio.CancelledError:
            await gdbus_proc.stop()
            break


def syncmain(db_file: Optional[Path] = None):
    db_path = Path(db_file) if db_file is not None else get_default_db_path()
    aiorun.run(mainloop(db_path), stop_on_unhandled_errors=True)


# Entry point: use `poetry run tailer` to execute
def main():
    fire.Fire(syncmain)
