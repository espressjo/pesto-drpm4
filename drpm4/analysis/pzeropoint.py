"""Photometric zero point (needs astrometry and the Gaia catalog)."""

from __future__ import annotations

from drpm4.ommobj import ommobj


class pzero:
    """Zero point of an observation. Not available yet: it needs astrometry (level 1) and Gaia."""

    def __init__(self, obj: ommobj) -> None:
        self.obj = obj

    def p_p0(self, filter: str | None = None, nb: int = 30):
        """Show the zero point for ``filter``."""
        raise NotImplementedError("the zero point needs astrometry and the Gaia catalog, not available yet")

    def dump_p0_in_database(self) -> None:
        """Record the average zero point in the database."""
        raise NotImplementedError("no database in this version of drpm4")
