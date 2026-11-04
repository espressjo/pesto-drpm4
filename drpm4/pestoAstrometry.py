"""Level 1: astrometry (WCS) of the level 0 files, see spec.md section 5.11.

A night series of one object moves very little, so a full solve of every frame
is a waste. The first frame is solved with astrometry.net; for the next ones the
stars of that frame are followed in small windows, and the solution is only
shifted (CRPIX) by the measured offset. A new full solve is done when the stars
cannot be followed any more, and every ``resolve_every`` frames to catch slow
rotation or scale changes.
"""

from __future__ import annotations

import logging
import os
import re
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import astropy.units as u
import numpy as np
from astropy.coordinates import SkyCoord
from astropy.io import fits
from astropy.stats import sigma_clipped_stats
from astropy.wcs import WCS
from photutils.detection import DAOStarFinder

from drpm4.pestoHeader import fits_files
from drpm4.tools.astroArray import register_offset
from drpm4.tools.configfile import load_config

log = logging.getLogger(__name__)

DRPCODE = "level-1"
_WCS_KEY = re.compile(
    r"^(WCSAXES|EQUINOX|LONPOLE|LATPOLE|CRVAL[12]|CRPIX[12]|CUNIT[12]|CTYPE[12]|CD[12]_[12]|PC[12]_[12]|CDELT[12]|"
    r"(A|B|AP|BP)_ORDER|(A|B|AP|BP)_\d+_\d+|ASTMETH|ASTLOGOD)$"
)


# --------------------------------------------------------------------------
# solver
# --------------------------------------------------------------------------
@dataclass
class SolveResult:
    """A WCS solution: the header cards, and the log-odds of the match."""

    header: fits.Header
    logodds: float


class Solver(Protocol):
    """Anything that can solve a list of star positions (the tests use a fake one)."""

    def solve(self, xy: np.ndarray, ra_deg: float | None = None, dec_deg: float | None = None) -> SolveResult | None:
        """Return the WCS of a field, or None. ``xy`` are 0-based pixel positions, brightest first."""


class AstrometrySolver:
    """The astrometry.net solver (python package ``astrometry``) with the local index files.

    The index files are read from ``[astrometry] index_dir`` of ``pesto.cfg``
    (default ``/usr/local/astrometry/data``). The solver is built once and reused.

    Raises
    ------
    FileNotFoundError
        If no index file is found.
    """

    def __init__(self, config=None) -> None:
        import astrometry

        self._astrometry = astrometry
        cfg = (config or load_config())["astrometry"]
        self.index_dir = Path(cfg["index_dir"])
        self.index_files = sorted(self.index_dir.glob(cfg["index_pattern"]))
        if not self.index_files:
            raise FileNotFoundError(
                f"no astrometry.net index file ({cfg['index_pattern']}) in {self.index_dir}; "
                "set [astrometry] index_dir in pesto.cfg"
            )
        self._solver = astrometry.Solver(self.index_files)
        self._size = astrometry.SizeHint(float(cfg["scale_low"]), float(cfg["scale_high"]))
        self._radius = float(cfg["position_radius"])
        self._params = dict(sip_order=int(cfg["sip_order"]))
        log.info("astrometry.net: %d index files in %s", len(self.index_files), self.index_dir)

    def solve(self, xy: np.ndarray, ra_deg: float | None = None, dec_deg: float | None = None) -> SolveResult | None:
        """Solve the field made of the stars ``xy`` (0-based pixels, brightest first)."""
        a = self._astrometry
        hint = None if ra_deg is None or dec_deg is None else a.PositionHint(ra_deg, dec_deg, self._radius)
        stars = [(float(x) + 1, float(y) + 1) for x, y in xy]  # FITS convention: first pixel is 1
        try:
            solution = self._solver.solve(
                stars=stars, size_hint=self._size, position_hint=hint,
                solution_parameters=a.SolutionParameters(**self._params),
            )
        except Exception as exc:  # noqa: BLE001 - e.g. no index file covers the position
            log.debug("solver error: %s", exc)
            return None
        if not solution.matches:
            return None
        best = max(solution.matches, key=lambda m: m.logodds)
        header = fits.Header()
        for key, (value, comment) in best.wcs_fields.items():
            header[key] = (value, comment)
        return SolveResult(header, float(best.logodds))


# --------------------------------------------------------------------------
# stars and following
# --------------------------------------------------------------------------
def detect_stars(image: np.ndarray, max_stars: int = 50, nsigma: float = 5.0) -> tuple[np.ndarray, float]:
    """Return the brightest stars as an (N, 2) array of 0-based (x, y), and the sky noise.

    Parameters
    ----------
    image : numpy.ndarray
        2D image.
    max_stars : int
        Number of stars kept, brightest first.
    nsigma : float
        Detection threshold above the sky noise.
    """
    _, med, std = (float(v) for v in sigma_clipped_stats(image))
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # "no sources found" is a normal outcome
        found = DAOStarFinder(fwhm=4.0, threshold=nsigma * std, exclude_border=True)(image - med)
    if found is None or len(found) == 0:
        return np.empty((0, 2)), std
    order = np.argsort(-np.asarray(found["flux"]))[:max_stars]
    return np.column_stack([np.asarray(found["x_centroid"])[order], np.asarray(found["y_centroid"])[order]]), std


def _centroids(image: np.ndarray, pos: np.ndarray, half: int, sigma: float) -> np.ndarray:
    """Re-centroid stars in (2*half+1)-pixel windows around ``pos``. NaN where it fails."""
    h, w = image.shape
    out = np.full(pos.shape, np.nan)
    yy, xx = np.mgrid[-half : half + 1, -half : half + 1]
    mask = np.hypot(xx, yy) <= half
    for k, (x, y) in enumerate(pos):
        for _ in range(2):  # the second pass re-centers the window
            xi, yi = int(round(x)), int(round(y))
            if xi - half < 0 or yi - half < 0 or xi + half + 1 > w or yi + half + 1 > h:
                x = y = np.nan
                break
            win = image[yi - half : yi + half + 1, xi - half : xi + half + 1]
            weights = np.clip(win - np.median(win) - 3 * sigma, 0, None) * mask
            total = weights.sum()
            if total <= 0:
                x = y = np.nan
                break
            x, y = xi + (weights * xx).sum() / total, yi + (weights * yy).sum() / total
        out[k] = (x, y)
    return out


def track_offset(
    ref_pos: np.ndarray, image: np.ndarray, guess: tuple[float, float], sigma: float,
    half: int = 8, min_stars: int = 8, max_rms: float = 0.4,
) -> tuple[tuple[float, float], float] | None:
    """Follow the reference stars in ``image``.

    Parameters
    ----------
    ref_pos : numpy.ndarray
        (N, 2) 0-based star positions in the reference frame.
    image : numpy.ndarray
        The new frame.
    guess : tuple of float
        Expected (dx, dy) of the field relative to the reference, in pixels.
    sigma : float
        Sky noise of ``image``.

    Returns
    -------
    ((dx, dy), rms) or None
        The offset and the scatter of the stars around it, or None when the stars
        cannot be followed consistently (too few, or too much scatter).
    """
    measured = _centroids(image, ref_pos + np.asarray(guess), half, sigma)
    d = measured - ref_pos
    d = d[np.isfinite(d).all(axis=1)]
    if len(d) < min_stars:
        return None
    inliers = d[np.hypot(*(d - np.median(d, axis=0)).T) < 0.8]
    if len(inliers) < min_stars or len(inliers) < 0.5 * len(d):
        return None
    offset = inliers.mean(axis=0)
    rms = float(np.sqrt(np.mean(np.sum((inliers - offset) ** 2, axis=1))))
    return ((float(offset[0]), float(offset[1])), rms) if rms <= max_rms else None


def shifted_header(wcs_header: fits.Header, offset: tuple[float, float]) -> fits.Header:
    """Return the WCS of a frame whose content moved by ``offset`` pixels relative to the solved one.

    Moving CRPIX is exact for the whole solution, SIP terms included, because
    they are expressed relative to CRPIX.
    """
    out = wcs_header.copy()
    out["CRPIX1"] = wcs_header["CRPIX1"] + offset[0]
    out["CRPIX2"] = wcs_header["CRPIX2"] + offset[1]
    return out


# --------------------------------------------------------------------------
# running level 1
# --------------------------------------------------------------------------
@dataclass
class Level1Result:
    """Outcome of level 1 on one folder."""

    folder: Path
    solved: int = 0  # frames that got a full astrometry.net solution
    tracked: int = 0  # frames that got the shifted solution
    skipped: int = 0
    unsolved: list[Path] = field(default_factory=list)
    failed: list[tuple[Path, str]] = field(default_factory=list)

    @property
    def done(self) -> int:
        """Number of frames with a WCS."""
        return self.solved + self.tracked


def _write(path: Path, data: np.ndarray, header: fits.Header, wcs_header: fits.Header, method: str, logodds: float) -> None:
    """Write ``path`` again with the WCS, the center RA/DEC and DRPCODE=level-1."""
    header = header.copy()
    for key in [k for k in header if _WCS_KEY.match(k)]:
        del header[key]
    for card in wcs_header.cards:
        header[card.keyword] = (card.value, card.comment)
    header["ASTMETH"] = (method, "solve: astrometry.net, track: shifted solution")
    header["ASTLOGOD"] = (round(logodds, 2), "log-odds of the astrometry.net solution")
    center = WCS(wcs_header).pixel_to_world((header["NAXIS1"] - 1) / 2, (header["NAXIS2"] - 1) / 2)
    ra_txt, dec_txt = center.to_string("hmsdms", precision=3).split(" ")
    header["RA"] = float(center.ra.hourangle)
    header["DEC"] = float(center.dec.deg)
    header["RATXT"], header["DECTXT"] = ra_txt, dec_txt
    header["DRPCODE"] = DRPCODE
    tmp = path.with_name("tmp-" + path.name)  # same extension, so .gz stays compressed
    fits.PrimaryHDU(data=data, header=header).writeto(tmp, overwrite=True)
    os.replace(tmp, path)


def run_level1(
    folder: Path, solver: Solver, config=None, overwrite: bool = True
) -> Level1Result:
    """Astrometry on every level 0 file of ``folder/redux``.

    The files are updated in place (WCS, RA/DEC of the field center, DRPCODE=level-1).
    A frame that cannot be solved is left at level 0.

    Parameters
    ----------
    folder : Path
        Object folder, its ``redux/`` must exist (run level 0 first).
    solver : Solver
        ``AstrometrySolver`` or anything with the same ``solve`` method.
    overwrite : bool
        If False, frames already at level 1 are skipped.
    """
    cfg = (config or load_config())["astrometry"]
    max_stars, nsigma = cfg.getint("max_stars"), cfg.getfloat("detect_sigma")
    resolve_every, half = cfg.getint("resolve_every"), cfg.getint("track_window")
    min_stars, max_rms, max_failures = cfg.getint("track_min_stars"), cfg.getfloat("track_max_rms"), cfg.getint("max_failures")

    result = Level1Result(folder=folder)
    redux = folder / "redux"
    files = fits_files(redux) if redux.is_dir() else []
    ref: SolveResult | None = None
    ref_pos = ref_img = None
    guess = (0.0, 0.0)
    since_solve = failures = 0

    for n, path in enumerate(files):
        try:
            with fits.open(path) as hdul:
                header, data = hdul[0].header.copy(), hdul[0].data
            if not overwrite and header.get("DRPCODE") == DRPCODE:
                result.skipped += 1
                continue
            image = np.asarray(data, dtype=float)
            wcs_header, method, logodds, offset = None, "", 0.0, (0.0, 0.0)
            sigma = None

            if ref is not None and since_solve < resolve_every:
                sigma = float(sigma_clipped_stats(image[::4, ::4])[2])  # sky noise, from a subsample: cheap
                for trial in (guess, None):
                    if trial is None:  # lost the field: coarse offset from the whole frame, then retry
                        trial = register_offset(ref_img, image, bin=4)
                    tracked = track_offset(ref_pos, image, trial, sigma, half, min_stars, max_rms)
                    if tracked:
                        offset, guess = tracked[0], tracked[0]
                        wcs_header, method, logodds = shifted_header(ref.header, offset), "track", ref.logodds
                        break

            if wcs_header is None:  # no reference yet, too old, or lost: full solve
                pos, sigma = detect_stars(image, max_stars, nsigma)
                ra, dec = header.get("RA", -999.99), header.get("DEC", -999.99)
                hint = (ra * 15, dec) if ra > -999 and dec > -999 else (None, None)
                found = solver.solve(pos, *hint) if len(pos) >= 4 else None
                if found is not None:
                    # Follow the stars with the same centroid estimator in every frame, the reference
                    # included: a different estimator would add a constant bias to every offset.
                    refined = _centroids(image, pos, half, sigma)
                    ref, ref_pos = found, refined[np.isfinite(refined).all(axis=1)]
                    ref_img, guess, since_solve = image, (0.0, 0.0), 0
                    wcs_header, method, logodds = found.header, "solve", found.logodds

            if wcs_header is None:
                result.unsolved.append(path)
                failures += 1
                if ref is None and failures >= max_failures:
                    log.warning("%s: %d solves failed in a row, giving up on this folder", folder.name, failures)
                    result.unsolved.extend(files[n + 1 :])
                    return result
                continue
            failures = 0
            since_solve += 1
            _write(path, data, header, wcs_header, method, logodds)
            if method == "solve":
                result.solved += 1
            else:
                result.tracked += 1
        except Exception as exc:  # noqa: BLE001 - one bad file must not stop the night
            log.error("%s: level 1 failed: %s", path, exc)
            result.failed.append((path, str(exc)))
    return result
