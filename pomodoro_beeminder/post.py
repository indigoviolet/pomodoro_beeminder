import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Generator, List, Optional, Tuple

import dateparser
import fire
import pytz
import requests
from more_itertools import windowed
from pysqlitedb import DB
from rich.console import Console

from .db import get_db, get_default_db_path

console = Console()
PACIFIC = pytz.timezone("America/Los_Angeles")
UTC = pytz.timezone("UTC")


def get_last_success_timestamp(db: DB) -> Optional[datetime]:
    result: Optional[sqlite3.Row] = db.execute(
        """
        SELECT posted_at FROM beeminder_posts
        WHERE error IS NULL
        ORDER BY posted_at DESC
        LIMIT 1
        """
    ).fetchone()
    if result is None:
        return None
    else:
        return datetime.fromisoformat(result["posted_at"])


def get_pomo_secs_in_interval(
    start_ts: Optional[datetime], end_ts: datetime, db: DB
) -> Generator[Tuple[datetime, float], None, None]:

    start_ts = start_ts or datetime.utcfromtimestamp(0)
    events: List[sqlite3.Row] = db.execute(
        """
        SELECT state, timestamp
        FROM pomo_state_changes
        WHERE timestamp >= :start_ts AND timestamp < :end_ts
        ORDER BY timestamp ASC
        """,
        {"start_ts": start_ts, "end_ts": end_ts},
    ).fetchall()

    # Spanning events are handled in the interval where they end. If
    # the first event within the interval is not a POMO, we fetch the
    # previous event - it could be a POMO (or a break)
    if len(events) > 0 and (first_event := events[0])["state"] != "POMO":
        spanning_events: List[sqlite3.Row] = db.execute(
            """
            SELECT state, timestamp
            FROM pomo_state_changes
            WHERE timestamp < :first_event_ts
            ORDER BY timestamp DESC
            LIMIT 1
            """,
            {"first_event_ts": first_event["timestamp"]},
        ).fetchall()
    else:
        spanning_events = []

    for prev, curr in windowed(spanning_events + events, 2):
        if prev is None or curr is None:
            break
        if prev["state"] == "POMO":
            prev_time, curr_time = [
                datetime.fromisoformat(t["timestamp"]) for t in (prev, curr)
            ]
            interval = (curr_time - prev_time).total_seconds()
            yield (curr_time, interval)


def post_to_beeminder(goal: str, ts: datetime, pomo_secs: float, posted_at: datetime):
    try:
        auth = json.loads(os.environ["BEEMINDER_AUTH"])
    except (KeyError, json.decoder.JSONDecodeError) as e:
        raise RuntimeError(
            "Invalid auth token BEEMINDER_AUTH: See https://www.beeminder.com/api/v1/auth_token.json"
        ) from e

    user = auth["username"]
    create_datapoint_url = (
        f"https://www.beeminder.com/api/v1/users/{user}/goals/{goal}/datapoints.json"
    )

    pomo_mins = pomo_secs / 60.0
    console.log(f"Posting to beeminder: [green]{pomo_mins}[/green] mins")

    # https://api.beeminder.com/#postdata
    response = requests.post(
        create_datapoint_url,
        data={
            "auth_token": auth["auth_token"],
            "timestamp": ts.timestamp(),
            "comment": f"{str(ts.astimezone(PACIFIC))} Posted at {str(posted_at.astimezone(PACIFIC))}",
            "value": pomo_mins,
            "requestid": f"{user}-{goal}-{ts}-{posted_at}",
        },
    )
    if response.ok:
        return
    else:
        response.raise_for_status()


def post(goal: str, since: Optional[str] = None, db_file: Optional[str] = None):
    db_path = Path(db_file) if db_file is not None else get_default_db_path()

    with get_db(db_path=db_path) as db:
        posted_at = db.utcnow()

        try:
            if since is not None:
                start_time = dateparser.parse(
                    since,
                    languages=["en"],
                    settings={"TIMEZONE": "America/Los_Angeles"},
                )
                assert start_time is not None, f"{since=} is parseable"
                start_time = start_time.astimezone(UTC)
            else:
                start_time = get_last_success_timestamp(db)
            for ts, pomo_secs in get_pomo_secs_in_interval(start_time, posted_at, db):
                console.log(f"{str(ts)} {pomo_secs=}")

                if pomo_secs > 0:
                    post_to_beeminder(goal, ts, pomo_secs, posted_at)

            # We don't want to mark as posted for 0.0 pomo_secs,
            # because then we will miss an ongoing pomo (which
            # would only be handled on ending)
            db.insert_row("beeminder_posts", {"posted_at": posted_at})

        except Exception as e:
            db.insert_row("beeminder_posts", {"posted_at": posted_at, "error": str(e)})
            raise


def main():
    fire.Fire(post)
