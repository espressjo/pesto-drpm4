"""Temperatures and humidity during an observation (from the headers)."""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

import numpy as np

from drpm4.analysis._common import frame_time, sample_indices, time_axis
from drpm4.ommobj import ommobj

log = logging.getLogger(__name__)

KEYS = ("TCCD", "TEMPM", "TEMPOUT", "TEMPIN", "HUMOUT", "HUMIN")


class temperature:
    """CCD temperature, mirror/exterior temperature and humidity of an observation.

    Parameters
    ----------
    obj : ommobj
        The observation.
    nb : int
        Number of files read (spread over the observation).
    """

    def __init__(self, obj: ommobj, nb: int = 30) -> None:
        self.obj = obj
        self.nb = nb
        self._data: dict[str, list] | None = None

    def measure(self) -> dict[str, list]:
        """Return a dict of lists: ``time`` plus TCCD, TEMPM, TEMPOUT, TEMPIN, HUMOUT, HUMIN.

        The -999 'missing' markers are turned into NaN.
        """
        if self._data is None:
            data: dict[str, list] = {"time": [], **{k: [] for k in KEYS}}
            for i in sample_indices(self.obj.number_of_file, self.nb):
                try:
                    h = self.obj.H(i, derive_text=False)
                    t = frame_time(self.obj, i)
                except Exception as exc:  # noqa: BLE001
                    log.warning("%s: no temperature (%s)", self.obj.ls[i].name, exc)
                    continue
                data["time"].append(t)
                for k in KEYS:
                    data[k].append(np.nan if h[k] <= -999 else h[k])
            self._data = data
        return self._data

    def _mean(self, key: str) -> float:
        values = np.array(self.measure()[key], dtype=float)
        return float(np.nanmean(values)) if values.size and not np.all(np.isnan(values)) else float("nan")

    def avg_ccd(self) -> float:
        """Average CCD temperature (C)."""
        return self._mean("TCCD")

    def avg_mirror(self) -> float:
        """Average mirror temperature (C)."""
        return self._mean("TEMPM")

    def avg_exterior(self) -> float:
        """Average exterior temperature (C)."""
        return self._mean("TEMPOUT")

    def _draw_ccd(self, ax) -> None:
        d = self.measure()
        ax.plot(d["time"], d["TCCD"], "o-", alpha=0.7)
        ax.set(ylabel="CCD temperature (°C)", title=f"CCD temperature: {self.obj.object} ({self.obj.filter})")
        time_axis(ax)

    def _draw_environment(self, ax) -> None:
        d = self.measure()
        ax2 = ax.twinx()
        lines = [
            ax.plot(d["time"], d["TEMPM"], "r-o", alpha=0.7, label="T mirror")[0],
            ax.plot(d["time"], d["TEMPOUT"], "g-o", alpha=0.7, label="T exterior")[0],
            ax2.plot(d["time"], d["HUMIN"], "y--", alpha=0.7, label="H interior")[0],
            ax2.plot(d["time"], d["HUMOUT"], "b--", alpha=0.7, label="H exterior")[0],
        ]
        ax.set(ylabel="Temperature (°C)", title=f"Environment: {self.obj.object}")
        ax2.set(ylabel="Humidity (%)", ylim=(0, 100))
        ax.legend(lines, [ln.get_label() for ln in lines], loc="best")
        time_axis(ax)

    def _png(self, draw, path: Path | str) -> Path:
        from matplotlib.figure import Figure

        fig = Figure(figsize=(6, 4), layout="constrained")
        draw(fig.subplots())
        fig.savefig(path, dpi=150)
        return Path(path)

    def create_ccd_png(self, path: Path | str) -> Path:
        """Save the CCD temperature plot as a PNG and return its path."""
        return self._png(self._draw_ccd, path)

    def create_environment_png(self, path: Path | str) -> Path:
        """Save the mirror/exterior temperature and humidity plot as a PNG."""
        return self._png(self._draw_environment, path)

    def show_ccd(self) -> None:
        """Show the CCD temperature plot."""
        import matplotlib.pyplot as plt

        _, ax = plt.subplots(layout="constrained")
        self._draw_ccd(ax)
        plt.show()

    def show_environment(self) -> None:
        """Show the temperature and humidity plot."""
        import matplotlib.pyplot as plt

        _, ax = plt.subplots(layout="constrained")
        self._draw_environment(ax)
        plt.show()
