"""Helpers shared by the analysis modules."""

from __future__ import annotations

from datetime import datetime

import numpy as np
from astropy.io import fits

from drpm4.ommobj import ommobj
from drpm4.tools import pTime
from drpm4.tools.configfile import load_config


def sample_indices(n: int, nb: int) -> list[int]:
    """Return at most ``nb`` indices spread evenly over ``n`` files."""
    if n <= nb:
        return list(range(n))
    return sorted(set(np.linspace(0, n - 1, nb, dtype=int).tolist()))


def pixel_scale() -> float:
    """Plate scale in arcsec per pixel, from ``pesto.cfg``."""
    return load_config().getfloat("instrument", "pixel_scale")


def read_frame(o: ommobj, i: int) -> np.ndarray:
    """Return frame ``i`` of the observation as a float array."""
    return np.asarray(fits.getdata(o.ls[i]), dtype=float)


def frame_time(o: ommobj, i: int) -> datetime:
    """Return the UT time (naive datetime) of frame ``i``."""
    utc = pTime.parse_utc(o.H(i, derive_text=False)["DATE"])
    if utc is None:
        raise ValueError(f"{o.ls[i].name}: no usable DATE")
    return utc.replace(tzinfo=None)


def time_axis(ax) -> None:
    """Format the x axis of ``ax`` as UT clock time."""
    import matplotlib.dates as mdates

    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    ax.set_xlabel("Time (UT)")
    for label in ax.get_xticklabels():
        label.set_rotation(-30)
        label.set_ha("left")
