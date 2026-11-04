"""Time helpers: ISO strings, night name, sidereal time."""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import astropy.units as u
from astropy.coordinates import EarthLocation
from astropy.time import Time

_ISO_START = re.compile(r"^\d{4}-\d{2}-\d{2}T")


def normalize_iso(text: str) -> str:
    """Normalize a timestamp to ``YYYY-MM-DDTHH:MM:SS.fff``.

    Handles a space instead of ``T``, a missing fraction, and the Nuvu
    ``HH:MM:SS:mmm`` form. Anything unrecognized is returned unchanged.
    """
    s = text.strip().replace(" ", "T", 1)
    if not _ISO_START.match(s):
        return text
    day, _, clock = s.partition("T")
    if clock.count(":") == 3:
        h, m, sec, frac = clock.split(":")
        clock = f"{h}:{m}:{sec}.{frac}"
    elif "." not in clock and clock.count(":") == 2:
        clock += ".000"
    return f"{day}T{clock}"


def parse_utc(text: str) -> datetime | None:
    """Parse an ISO timestamp as UTC. Return None if it cannot be parsed."""
    try:
        dt = datetime.fromisoformat(normalize_iso(str(text)))
    except ValueError:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def night_date(utc: datetime, tz_name: str) -> date:
    """Return the night a UTC instant belongs to.

    The night changes at *local* noon: before 12:00 local time the instant
    belongs to the previous day's night.
    """
    local = utc.astimezone(ZoneInfo(tz_name))
    return (local - timedelta(days=1)).date() if local.hour < 12 else local.date()


def ut_hours(utc: datetime) -> float:
    """Return the UTC time of day in decimal hours."""
    return utc.hour + utc.minute / 60 + (utc.second + utc.microsecond / 1e6) / 3600


def sidereal_hours(utc: datetime, lat: float, lon: float, height: float) -> float:
    """Return the local mean sidereal time in hours."""
    loc = EarthLocation.from_geodetic(lat=lat * u.deg, lon=lon * u.deg, height=height * u.m)
    return float(Time(utc, location=loc).sidereal_time("mean").hour)
