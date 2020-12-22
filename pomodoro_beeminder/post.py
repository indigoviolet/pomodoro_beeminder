import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import List, Optional

import fire
import pytz
import requests
from more_itertools import windowed
from pysqlitedb import DB
from snoop import pp

from .db import get_db, get_default_db_path

PACIFIC = pytz.timezone("America/Los_Angeles")


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
) -> float:

    start_ts = start_ts or datetime.utcfromtimestamp(0)
    events: List[sqlite3.Row] = db.execute(
        """
        SELECT state, timestamp
        FROM pomo_state_changes
        WHERE timestamp >= :start_ts AND timestamp <= :end_ts
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
            WHERE timestamp <= :first_event_ts
            ORDER BY timestamp DESC
            LIMIT 1
            """,
            {"first_event_ts": first_event["timestamp"]},
        ).fetchall()
    else:
        spanning_events = []

    pomo_secs = 0.0
    for prev, curr in windowed(spanning_events + events, 2):
        if prev is None or curr is None:
            break
        if prev["state"] == "POMO":
            interval = datetime.fromisoformat(
                curr["timestamp"]
            ) - datetime.fromisoformat(prev["timestamp"])
            pomo_secs += interval.total_seconds()

    return pomo_secs


def post_to_beeminder(goal: str, pomo_secs: float, posted_at: datetime):
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
    pp(pomo_mins)

    # https://api.beeminder.com/#postdata
    response = requests.post(
        create_datapoint_url,
        data={
            "auth_token": auth["auth_token"],
            "timestamp": posted_at.timestamp(),
            "comment": str(posted_at.astimezone(PACIFIC)),
            "value": pomo_mins,
            "requestid": f"{user}-{goal}-{posted_at}",
        },
    )
    if response.ok:
        return
    else:
        response.raise_for_status()


def post(goal: str, db_file: Optional[str] = None):
    db_path = Path(db_file) if db_file is not None else get_default_db_path()

    with get_db(db_path=db_path) as db:
        posted_at = db.utcnow()

        try:
            last_success = get_last_success_timestamp(db)
            pomo_secs = get_pomo_secs_in_interval(last_success, posted_at, db)
            if pomo_secs > 0:
                post_to_beeminder(goal, pomo_secs, posted_at)

                # We don't want to mark as posted for 0.0 pomo_secs,
                # because then we will miss an ongoing pomo (which
                # would only be handled on ending)
                db.insert_row("beeminder_posts", {"posted_at": posted_at})
            else:
                pp("No pomo time found", pomo_secs)

        except Exception as e:
            db.insert_row("beeminder_posts", {"posted_at": posted_at, "error": str(e)})
            raise


def main():
    fire.Fire(post)
