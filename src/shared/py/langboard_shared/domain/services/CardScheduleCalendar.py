"""Schedule query validation, scope-bound cursors and RFC 5545 serialization."""

import base64
import json
from datetime import datetime, timedelta, timezone
from hashlib import sha256


def schedule_range(start: str, end: str):
    def parse(value):
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if result.utcoffset() is None:
            raise ValueError("Schedule timestamps require an explicit UTC offset")
        return result.astimezone(timezone.utc)

    start_at, end_at = parse(start), parse(end)
    if not start_at < end_at or end_at - start_at > timedelta(days=93):
        raise ValueError("Schedule range must be positive and at most 93 days")
    return start_at, end_at


def schedule_key(actor, scope, project_uid, start, end):
    return sha256(json.dumps([str(actor), scope, project_uid, start.isoformat(), end.isoformat()]).encode()).hexdigest()


def schedule_cursor(key, values=None, cursor=None):
    if cursor is None:
        return base64.urlsafe_b64encode(json.dumps([key, *values], separators=(",", ":")).encode()).decode().rstrip("=")
    try:
        if not isinstance(cursor, str) or len(cursor) > 512:
            raise ValueError
        fields = json.loads(base64.b64decode(cursor + "=" * (-len(cursor) % 4), altchars=b"-_", validate=True))
        if not isinstance(fields, list) or len(fields) != 4 or fields[0] != key:
            raise ValueError
        timestamp, _ = schedule_range(fields[1], (datetime.fromisoformat(fields[1]) + timedelta(seconds=1)).isoformat())
        ids = fields[2:]
        if any(type(value) is not int or value <= 0 for value in ids):
            raise ValueError
        return timestamp, *ids
    except (ValueError, TypeError, OverflowError) as exc:
        raise ValueError("Invalid Schedule cursor") from exc


def calendar_bytes(items, *, now=None):
    """A deadline is a UTC VEVENT instant; it implies no duration or completion."""

    def utc(value):
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    def text(value):
        value = value.replace("\r\n", "\n").replace("\r", "\n")
        value = "".join(char for char in value if char == "\n" or ord(char) >= 32)
        return value.replace("\\", "\\\\").replace("\n", "\\n").replace(";", "\\;").replace(",", "\\,")

    stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Langboard//Schedule//EN", "CALSCALE:GREGORIAN"]
    for item in items:
        lines.extend(
            [
                "BEGIN:VEVENT",
                f"UID:card-{item['card_uid']}@langboard",
                "DTSTAMP:" + stamp,
                "DTSTART:" + utc(item["deadline_at"]),
                "SUMMARY:" + text(item["title"]),
                "END:VEVENT",
            ]
        )
    lines.append("END:VCALENDAR")
    folded = []
    for line in lines:
        current = ""
        length = 0
        for char in line:
            size = len(char.encode("utf-8"))
            if length + size > 75:
                folded.append(current)
                current = " "
                length = 1
            current += char
            length += size
        folded.append(current)
    return ("\r\n".join(folded) + "\r\n").encode("utf-8")
