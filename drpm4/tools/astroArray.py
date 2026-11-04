"""numpy array with a region of interest and basic star statistics.

Ported from drpm3. ``astroArray(data)`` wraps an image (or a FITS file name);
``roi(x, y, size)`` cuts a region that remembers where it came from, so that
centroids can be returned in the coordinates of the full frame.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from astropy.io import fits
from astropy.stats import sigma_clipped_stats
from photutils.centroids import centroid_quadratic
from photutils.profiles import RadialProfile
from scipy.ndimage import gaussian_filter


class astroArrayClass(np.ndarray):
    """ndarray that knows the offset of its region of interest in the full frame."""

    _x: int = 0
    _y: int = 0

    def __array_finalize__(self, obj) -> None:
        self._x = getattr(obj, "_x", 0)
        self._y = getattr(obj, "_y", 0)

    def get(self, x: float, y: float) -> tuple[float, float]:
        """Return the full-frame coordinates of a point given in this region's coordinates."""
        return self._x + x, self._y + y

    def roi(self, x: float, y: float, size: int) -> astroArrayClass:
        """Return a square region of interest of ``size`` pixels centered on (x, y)."""
        x, y, half = int(x), int(y), int(size / 2)
        y1, y2 = max(y - half, 0), min(y + half, self.shape[0] - 1)
        x1, x2 = max(x - half, 0), min(x + half, self.shape[1] - 1)
        out = np.asarray(self[y1:y2, x1:x2]).view(astroArrayClass)
        out._x, out._y = x1, y1
        return out

    def moment(self, relatif: bool = False) -> tuple[float, float]:
        """Return the centroid of the pixels brighter than 4 sigma above the background."""
        a = np.array(self, dtype=float)
        mean, _, std = sigma_clipped_stats(a)
        a[a < mean + 4 * std] = 0
        total = a.sum()
        if total == 0:
            return np.nan, np.nan
        yy, xx = np.indices(a.shape)
        cx, cy = (xx * a).sum() / total, (yy * a).sum() / total
        return (cx, cy) if relatif else (self._x + cx, self._y + cy)

    def quad(self, relatif: bool = False) -> tuple[float, float]:
        """Return the quadratic-fit centroid (photutils)."""
        x, y = centroid_quadratic(np.asarray(self, dtype=float), fit_boxsize=7)
        return (x, y) if relatif else (self._x + x, self._y + y)

    def center_of_mass_weighted(self, alpha: float = 2.0, relatif: bool = False) -> tuple[float, float]:
        """Return the center of mass with the pixel values raised to ``alpha`` as extra weight."""
        im = np.array(self, dtype=float)
        weights = np.power(np.clip(im, 0, None), alpha)
        weights = weights / weights.max()
        yy, xx = np.indices(im.shape)
        w = im * weights
        cx, cy = (xx * w).sum() / w.sum(), (yy * w).sum() / w.sum()
        return (cx, cy) if relatif else (self._x + cx, self._y + cy)

    def fwhm(self, scale: float = 1.0, force_com: bool = False) -> float:
        """Return the FWHM of the star in the region, times ``scale`` (arcsec per pixel).

        The region must be centered on a background-subtracted star and at least
        40 pixels wide. Returns NaN if the profile cannot be fitted.
        """
        try:
            x, y = self.moment(relatif=True)
            if not force_com and not np.isnan(x):
                x, y = centroid_quadratic(np.asarray(self, dtype=float), fit_boxsize=7)
            if np.isnan(x) or np.isnan(y):
                x, y = self.center_of_mass_weighted(5.0, True)
            rp = RadialProfile(np.asarray(self, dtype=float), (x, y), np.arange(20))
            return float(rp.gaussian_fwhm) * scale
        except Exception:  # noqa: BLE001 - a bad star must not stop the night
            return float("nan")


def astroArray(array: np.ndarray | Path | str, dtype=float) -> astroArrayClass:
    """Wrap an image, or a FITS file name, as an ``astroArrayClass``."""
    if isinstance(array, (str, Path)):
        array = fits.getdata(array)
    return np.asarray(array, dtype=dtype).view(astroArrayClass)


def _binned(a: np.ndarray, n: int) -> np.ndarray:
    h, w = (a.shape[0] // n) * n, (a.shape[1] // n) * n
    return a[:h, :w].reshape(h // n, n, w // n, n).mean(axis=(1, 3))


def register_offset(ref: np.ndarray, img: np.ndarray, bin: int = 1) -> tuple[float, float]:
    """Return the (dx, dy) shift, in pixels, of the content of ``img`` relative to ``ref``.

    Phase correlation on lightly smoothed images, with parabolic sub-pixel
    refinement. To align ``img`` on ``ref`` shift it by ``(-dx, -dy)``.
    ``bin`` > 1 bins both images first: much faster, accurate to about a binned pixel.
    """
    if bin > 1:
        dx, dy = register_offset(_binned(np.asarray(ref, float), bin), _binned(np.asarray(img, float), bin))
        return dx * bin, dy * bin

    def prep(a: np.ndarray) -> np.ndarray:
        a = np.asarray(a, dtype=float)
        a = gaussian_filter(a - np.median(a), 1.5)
        return a * np.outer(np.hanning(a.shape[0]), np.hanning(a.shape[1]))

    cross = np.fft.fft2(prep(ref)) * np.conj(np.fft.fft2(prep(img)))
    cross /= np.abs(cross) + 1e-12
    corr = np.fft.ifft2(cross).real
    py, px = np.unravel_index(np.argmax(corr), corr.shape)

    def refine(c_m: float, c_0: float, c_p: float) -> float:
        denom = c_m - 2 * c_0 + c_p
        return 0.0 if denom == 0 else 0.5 * (c_m - c_p) / denom

    h, w = corr.shape
    dx = px + refine(corr[py, (px - 1) % w], corr[py, px], corr[py, (px + 1) % w])
    dy = py + refine(corr[(py - 1) % h, px], corr[py, px], corr[(py + 1) % h, px])
    if dx > w / 2:
        dx -= w
    if dy > h / 2:
        dy -= h
    return -float(dx), -float(dy)
