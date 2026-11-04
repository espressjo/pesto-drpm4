"""Seeing during an observation, from the FWHM of the stars."""

from __future__ import annotations

import logging
import warnings
from datetime import datetime
from pathlib import Path

import numpy as np
from astropy.stats import sigma_clipped_stats
from photutils.detection import DAOStarFinder

from drpm4.analysis._common import frame_time, pixel_scale, read_frame, sample_indices, time_axis
from drpm4.ommobj import ommobj
from drpm4.tools.astroArray import astroArray

log = logging.getLogger(__name__)

ROI_SIZE = 40  # pixels
EDGE = 30  # stars closer than this to the border are ignored
MAX_STARS = 25
FWHM_RANGE = (0.3, 10.0)  # arcsec, anything outside is a bad fit
SATURATION = 60000.0


def image_seeing(
    image: np.ndarray, scale: float = 0.455, threshold_sigma: float = 8.0
) -> tuple[float, float, int]:
    """Measure the seeing of one image.

    Parameters
    ----------
    image : numpy.ndarray
        2D image.
    scale : float
        Plate scale, arcsec per pixel.
    threshold_sigma : float
        Detection threshold above the background noise.

    Returns
    -------
    fwhm, fwhm_err, nb_stars : float, float, int
        Median FWHM and its scatter, in arcsec, and the number of stars used.
        ``fwhm`` is NaN if no usable star was found.
    """
    im = astroArray(image)
    _, med, std = (float(v) for v in sigma_clipped_stats(np.asarray(im)))
    im = im - med
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # "no sources found" is a normal outcome
        found = DAOStarFinder(fwhm=4.0, threshold=threshold_sigma * std, exclude_border=True)(np.asarray(im))
    if found is None or len(found) == 0:
        return np.nan, 0.0, 0
    xy = np.column_stack([found["x_centroid"], found["y_centroid"]])
    h, w = im.shape
    fwhm: list[float] = []
    for row in np.argsort(-np.asarray(found["flux"])):
        x, y = xy[row]
        if not (EDGE < x < w - EDGE and EDGE < y < h - EDGE) or found["peak"][row] > SATURATION:
            continue
        others = np.delete(xy, row, axis=0)
        if len(others) and np.min(np.hypot(*(others - xy[row]).T)) < ROI_SIZE / 2:
            continue  # blended with a neighbour
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")  # photutils warns about every marginal fit
            value = im.roi(x, y, ROI_SIZE).fwhm(scale)
        if FWHM_RANGE[0] < value < FWHM_RANGE[1]:
            fwhm.append(value)
        if len(fwhm) >= MAX_STARS:
            break
    if not fwhm:
        return np.nan, 0.0, 0
    if len(fwhm) > 3:
        mean, _, std = sigma_clipped_stats(fwhm)
        return float(mean), float(std), len(fwhm)
    return float(np.mean(fwhm)), 0.0, len(fwhm)


class seeing:
    """Seeing of an observation, measured on frames spread over the observation.

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
        self._data: tuple[list[datetime], list[float], list[float]] | None = None

    def measure(self, nb: int | None = None) -> tuple[list[datetime], list[float], list[float]]:
        """Return (times, fwhm, fwhm_err), fwhm in arcsec. Frames without a usable star are left out."""
        nb = nb or self.nb
        if self._data is not None and nb == self.nb:
            return self._data
        scale = pixel_scale()
        times: list[datetime] = []
        fwhm: list[float] = []
        err: list[float] = []
        for i in sample_indices(self.obj.number_of_file, nb):
            try:
                f, e, n = image_seeing(read_frame(self.obj, i), scale)
                if np.isnan(f):
                    continue
                times.append(frame_time(self.obj, i))
            except Exception as exc:  # noqa: BLE001
                log.warning("%s: seeing failed (%s)", self.obj.ls[i].name, exc)
                continue
            fwhm.append(f)
            err.append(e)
        if nb == self.nb:
            self._data = (times, fwhm, err)
        return times, fwhm, err

    def avg_seeing(self) -> float:
        """Return the average seeing (arcsec) over the observation, NaN if it could not be measured."""
        _, fwhm, _ = self.measure()
        return float(np.mean(fwhm)) if fwhm else float("nan")

    def _draw(self, ax, nb: int | None = None) -> None:
        times, fwhm, err = self.measure(nb)
        ax.errorbar(times, fwhm, yerr=err, fmt="o-", alpha=0.7)
        ax.set_ylim(0, max(6.0, (max(fwhm) if fwhm else 0) * 1.2))
        ax.set(ylabel='FWHM (")', title=f"Seeing: {self.obj.object} ({self.obj.filter})")
        if not fwhm:
            ax.text(0.5, 0.5, "no measurement", transform=ax.transAxes, ha="center")
        time_axis(ax)

    def create_seeing_png(self, path: Path | str) -> Path:
        """Save the seeing plot as a PNG and return its path."""
        from matplotlib.figure import Figure

        fig = Figure(figsize=(6, 4), layout="constrained")
        self._draw(fig.subplots())
        fig.savefig(path, dpi=150)
        return Path(path)

    def show_seeing(self, nb: int = 30) -> None:
        """Show the seeing plot with ``nb`` points."""
        import matplotlib.pyplot as plt

        fig, ax = plt.subplots(layout="constrained")
        self._draw(ax, nb)
        plt.show()
