"""Quality of the telescope tracking during an observation."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np

from drpm4.analysis._common import frame_time, pixel_scale, read_frame, sample_indices, time_axis
from drpm4.ommobj import ommobj
from drpm4.tools.astroArray import register_offset

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class TrackingQuality:
    """Tracking statistics, all in arcsec (``drift_per_hour`` in arcsec/hour)."""

    rms: float  # scatter of the position around its mean
    max_offset: float  # largest excursion from the first frame
    drift_per_hour: float  # speed of the linear drift


class ptracking:
    """Tracking of an observation: how far the field moves on the detector.

    The offset of each sampled frame relative to the first one is measured by
    phase correlation of the whole frame, so no astrometry is needed.

    Parameters
    ----------
    obj : ommobj
        The observation.
    nb : int
        Number of frames measured.
    """

    def __init__(self, obj: ommobj, nb: int = 30) -> None:
        self.obj = obj
        self.nb = nb
        self._data: tuple[list[datetime], np.ndarray, np.ndarray] | None = None

    def measure(self) -> tuple[list[datetime], np.ndarray, np.ndarray]:
        """Return (times, dx, dy), offsets in arcsec relative to the first sampled frame."""
        if self._data is None:
            scale = pixel_scale()
            ref = None
            times, dx, dy = [], [], []
            for i in sample_indices(self.obj.number_of_file, self.nb):
                try:
                    frame = read_frame(self.obj, i)
                    t = frame_time(self.obj, i)
                    if ref is None:
                        ref, offset = frame, (0.0, 0.0)
                    else:
                        offset = register_offset(ref, frame)
                except Exception as exc:  # noqa: BLE001
                    log.warning("%s: tracking failed (%s)", self.obj.ls[i].name, exc)
                    continue
                times.append(t)
                dx.append(offset[0] * scale)
                dy.append(offset[1] * scale)
            self._data = (times, np.array(dx), np.array(dy))
        return self._data

    @property
    def t_quality(self) -> TrackingQuality | None:
        """Return the tracking statistics, None if fewer than 3 frames could be measured."""
        times, dx, dy = self.measure()
        if len(times) < 3:
            return None
        hours = np.array([(t - times[0]).total_seconds() for t in times]) / 3600
        rms = float(np.sqrt(np.var(dx) + np.var(dy)))
        max_offset = float(np.max(np.hypot(dx, dy)))
        if hours[-1] > 0:
            drift = float(np.hypot(np.polyfit(hours, dx, 1)[0], np.polyfit(hours, dy, 1)[0]))
        else:
            drift = 0.0
        return TrackingQuality(rms, max_offset, drift)

    def _draw(self, ax) -> None:
        times, dx, dy = self.measure()
        ax.plot(times, dx, "r-o", alpha=0.7, label="x")
        ax.plot(times, dy, "y-o", alpha=0.7, label="y")
        ax.set(ylabel="Offset (arcsec)", title=f"Tracking: {self.obj.object} ({self.obj.filter})")
        ax.legend()
        time_axis(ax)

    def create_tracking_png(self, path: Path | str) -> Path:
        """Save the tracking plot as a PNG and return its path."""
        from matplotlib.figure import Figure

        fig = Figure(figsize=(6, 4), layout="constrained")
        self._draw(fig.subplots())
        fig.savefig(path, dpi=150)
        return Path(path)

    def show_tracking(self) -> None:
        """Show the tracking plot."""
        import matplotlib.pyplot as plt

        _, ax = plt.subplots(layout="constrained")
        self._draw(ax)
        plt.show()
