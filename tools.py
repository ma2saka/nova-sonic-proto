"""Tools exposed to Sonic: a toolSpec for the model plus a local implementation."""

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    schema: dict
    run: Callable[[dict], dict]

    def spec(self) -> dict:
        return {"name": self.name, "description": self.description, "inputSchema": {"json": json.dumps(self.schema)}}


def current_datetime(_: dict) -> dict:
    now = datetime.now().astimezone()
    return {"datetime": now.isoformat(timespec="minutes"), "weekday": now.strftime("%A"), "timezone": now.tzname()}


TOOLS = {
    t.name: t
    for t in [
        Tool(
            name="getCurrentDateTime",
            description="Returns the current local date, time, weekday and timezone. Use it whenever the user asks about today, now, or relative dates.",
            schema={"type": "object", "properties": {}, "required": []},
            run=current_datetime,
        ),
    ]
}
