"""Parse a night folder into observations (``ommobj``), see spec.md section 2.4."""

from __future__ import annotations

import logging
import math
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from typing import Any

from astropy.io import fits

from drpm4.pestoHeader import HeaderReference, fits_files
from drpm4.tools import pTime
from drpm4.tools.camera import get_camera
from drpm4.tools.configfile import load_config

log = logging.getLogger(__name__)

CALIBRATION_DIRS = ("Dark", "DomeFlat", "SkyFlat")
POSITION_TOL_ARCMIN = 4.0  # same name but farther apart than this is a different object
ROTATION_TOL_DEG = 0.01
EXPTIME_RTOL = 1e-3  # a dark matches an observation if the exposure times agree to 0.1 %


class ommobj(ABC):
    """One observation: same object folder, filter, rotation and position.

    Attributes
    ----------
    path : Path
        Folder of the observation.
    ls : list of Path
        All the files, in natural order.
    object, filter : str
    rotation : float
        FOV rotator angle (PESTOROT), degrees.
    foyer : float
        Focus position of the first file.
    date : datetime
        UT date of the first file.
    night : int
        Night name (YYMMDD).
    mosaic : bool, mosaic_id : int or None
        Not determined yet, see spec.md.
    flat, dark : list of Path
        Associated calibration files (filled by ``ommobjs.parse``).
    exposure_time : float
        Total exposure time of the observation, in seconds.
    """

    def __init__(self, ls: list[Path]) -> None:
        self.path: Path = Path()
        self.ls: list[Path] = []
        self.object = ""
        self.filter = ""
        self.rotation = -999.99
        self.foyer = -999.99
        self.ra = -999.99  # hours, first file
        self.dec = -999.99  # degrees, first file
        self.night = -999
        self.date: datetime | None = None
        self.texp = -999.99  # requested exposure of the first file, ms
        self.exposure_time = 0.0
        self.mosaic = False
        self.mosaic_id: int | None = None
        self.flat: list[Path] = []
        self.dark: list[Path] = []
        self._mode: dict[str, Any] = {}
        self.parse(ls)

    # -- to be provided by the data source ---------------------------------
    @abstractmethod
    def parse(self, ls: list[Path]) -> None:
        """Given a list of files, parse the information of the observation."""

    @abstractmethod
    def H(self, n: int, derive_text: bool = True) -> fits.Header:
        """Return the standardized header of file ``n`` (index in ``ls``).

        ``derive_text=False`` skips RATXT/DECTXT, which is much faster when many headers are read.
        """

    @abstractmethod
    def dump_in_db(self) -> None:
        """Dump the information of the observation in the database."""

    # -- common --------------------------------------------------------------
    @property
    def number_of_file(self) -> int:
        """Number of files in the observation."""
        return len(self.ls)

    @staticmethod
    def _increment(p: Path) -> int | None:
        try:
            return int(p.name.split("_")[1].split(".")[0])
        except (IndexError, ValueError):
            return None

    @property
    def start(self) -> int | None:
        """First increment."""
        return self._increment(self.ls[0]) if self.ls else None

    @property
    def stop(self) -> int | None:
        """Last increment."""
        return self._increment(self.ls[-1]) if self.ls else None

    @property
    def mode(self) -> dict[str, Any]:
        """Readout mode of the first file (amplifier, frequencies, gain, binning, crop, overscan)."""
        return dict(self._mode)

    def ccdt(self, n: int) -> float:
        """Return the CCD temperature (C) of file ``n``."""
        return self.H(n)["TCCD"]

    def show(self, n: int) -> None:
        """Display file ``n`` (zscale)."""
        from drpm4.tools.drpShow import show_image

        show_image(fits.getdata(self.ls[n]), f"{self.object} {self.filter} [{n}] {self.ls[n].name}")

    def info(self) -> str:
        """Return the information about the observation as text."""
        return (
            f"object   : {self.object}\n"
            f"path     : {self.path}\n"
            f"night    : {self.night}\n"
            f"date     : {self.date}\n"
            f"files    : {self.number_of_file} (increments {self.start}-{self.stop})\n"
            f"filter   : {self.filter}\n"
            f"rotation : {self.rotation}\n"
            f"foyer    : {self.foyer}\n"
            f"RA, DEC  : {self.ra}, {self.dec}\n"
            f"exposure : {self.texp / 1000:g} s x {self.number_of_file} (total {self.exposure_time:g} s)\n"
            f"mode     : {self._mode}\n"
            f"mosaic   : {self.mosaic} ({self.mosaic_id})\n"
            f"flat     : {len(self.flat)} file(s)\n"
            f"dark     : {len(self.dark)} file(s)"
        )

    def printInfo(self) -> None:
        """Print the information about the observation."""
        print(self.info())

    def __repr__(self) -> str:
        return f"<{type(self).__name__} {self.object} {self.filter} rot={self.rotation} n={self.number_of_file}>"


class ommobjFits(ommobj):
    """An observation read from raw FITS files.

    Parameters
    ----------
    ls : list of Path
        Files of the observation.
    reference, config : optional
        Header definition and configuration (loaded when omitted).
    headers : dict, optional
        Already standardized headers (``derive_text=False``) by file, to avoid reading them twice.
    """

    def __init__(
        self,
        ls: list[Path],
        reference: HeaderReference | None = None,
        config=None,
        headers: dict[Path, fits.Header] | None = None,
    ) -> None:
        self._reference = reference or HeaderReference.load()
        self._config = config or load_config()
        self._light = dict(headers or {})
        super().__init__(ls)

    def _standard(self, f: Path, derive_text: bool) -> fits.Header:
        raw = fits.getheader(f)
        camera = get_camera(raw)
        if camera == "unknown":
            raise ValueError("unknown camera")
        return self._reference.standardize(
            raw, camera, filename=f, datatype="SCIENCE", derive_text=derive_text, config=self._config
        )

    def _quick(self, f: Path) -> fits.Header:
        if f not in self._light:
            self._light[f] = self._standard(f, derive_text=False)
        return self._light[f]

    def parse(self, ls: list[Path]) -> None:
        """Read the headers of ``ls`` and fill the attributes."""
        self.ls = _natural_sorted([Path(f) for f in ls])
        if not self.ls:
            raise ValueError("an observation needs at least one file")
        h = self._quick(self.ls[0])
        self.path = self.ls[0].parent
        self.object = self.path.name
        self.filter, self.rotation = h["FILTER"], h["PESTOROT"]
        self.foyer, self.ra, self.dec = h["FOCUS"], h["RA"], h["DEC"]
        self.night, self.texp = h["NIGHTID"], h["TEXP"]
        self.date = pTime.parse_utc(h["DATE"])
        self._mode = {
            "amp_type": h["AMP_TYPE"],
            "h_freq": h["H_FREQ"],
            "v_freq": h["V_FREQ"],
            "ana_gain": h["ANA_GAIN"],
            "kgain": h["KGAIN_01"],
            "binning": (h["BIN_X"], h["BIN_Y"]),
            "crop": h["CRP_ENAB"],
            "overscan": h["OVS"],
        }
        total_ms = 0.0
        for f in self.ls:
            try:
                total_ms += max(self._quick(f)["TEXP"], 0.0)
            except Exception as exc:  # noqa: BLE001
                log.warning("%s: no exposure time (%s)", f, exc)
        self.exposure_time = total_ms / 1000

    def H(self, n: int, derive_text: bool = True) -> fits.Header:
        """Return the standardized header of file ``n`` (index in ``ls``)."""
        if not derive_text:
            return self._quick(self.ls[n])
        return self._standard(self.ls[n], derive_text=True)

    def dump_in_db(self) -> None:
        raise NotImplementedError("no database in this version of drpm4")


# --------------------------------------------------------------------------
def _natural_sorted(files: list[Path]) -> list[Path]:
    import re

    return sorted(files, key=lambda p: [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", p.name)])


def _separation_arcmin(ra1: float, dec1: float, ra2: float, dec2: float) -> float:
    """Angular separation between two positions (RA in hours, DEC in degrees)."""
    a1, a2 = math.radians(ra1 * 15), math.radians(ra2 * 15)
    d1, d2 = math.radians(dec1), math.radians(dec2)
    cos_sep = math.sin(d1) * math.sin(d2) + math.cos(d1) * math.cos(d2) * math.cos(a1 - a2)
    return math.degrees(math.acos(max(-1.0, min(1.0, cos_sep)))) * 60


def _same_observation(g: dict, filt: str, rot: float, ra: float, dec: float) -> bool:
    if g["filter"] != filt or abs(g["rotation"] - rot) > ROTATION_TOL_DEG:
        return False
    if min(g["ra"], ra, g["dec"], dec) <= -999:  # position unknown: cannot split on it
        return True
    return _separation_arcmin(g["ra"], g["dec"], ra, dec) <= POSITION_TOL_ARCMIN


class ommobjs:
    """All the observations of a night.

    Parameters
    ----------
    reference, config : optional
        Header definition and configuration (loaded when omitted).

    Attributes
    ----------
    objects : list of ommobj
        Filled by ``parse``.
    """

    def __init__(self, reference: HeaderReference | None = None, config=None) -> None:
        self._reference = reference or HeaderReference.load()
        self._config = config or load_config()
        self.night_dir: Path | None = None
        self.objects: list[ommobj] = []

    # ---------------------------------------------------------------------
    @property
    def night(self) -> str:
        """Night name, YYMMDD."""
        for o in self.objects:
            if o.night > 0:
                return str(o.night)
        return self.night_dir.name if self.night_dir else ""

    @property
    def nb_objects(self) -> int:
        """Number of observations found."""
        return len(self.objects)

    @property
    def observing_time(self) -> float:
        """Time spent observing with PESTO, in hours (sum of the exposure times)."""
        return sum(o.exposure_time for o in self.objects) / 3600

    @property
    def temperature(self):
        """Temperature of the whole night, from the database (not available yet)."""
        raise NotImplementedError("the night temperature comes from the database, not available yet")

    def __iter__(self):
        return iter(self.objects)

    def __len__(self) -> int:
        return len(self.objects)

    def __getitem__(self, i: int) -> ommobj:
        return self.objects[i]

    # ---------------------------------------------------------------------
    def parse(self, night_dir: Path | str, only: Path | None = None) -> list[ommobj]:
        """Parse the night folder and return its observations.

        Scans ``night_dir/Target``. Files of one object folder are split into
        observations on filter, rotation and position (> 4 arcmin). The
        calibrations (DomeFlat, Dark) are associated to each observation.

        Parameters
        ----------
        night_dir : Path or str
            Night folder (``yymmdd``).
        only : Path, optional
            Restrict to this single object folder.

        Returns
        -------
        list of ommobj
        """
        night_dir = Path(night_dir)
        self.night_dir = night_dir
        target = night_dir / "Target"
        folders = [only] if only else (sorted(p for p in target.iterdir() if p.is_dir()) if target.is_dir() else [])
        self.objects = []
        for folder in folders:
            groups: list[dict] = []
            headers: dict[Path, fits.Header] = {}
            for f in fits_files(folder):
                try:
                    raw = fits.getheader(f)
                    camera = get_camera(raw)
                    if camera == "unknown":
                        raise ValueError("unknown camera")
                    H = self._reference.standardize(
                        raw, camera, filename=f, datatype="SCIENCE", derive_text=False, config=self._config
                    )
                except Exception as exc:  # noqa: BLE001
                    log.warning("%s: ignored while scanning (%s)", f, exc)
                    continue
                headers[f] = H
                for g in groups:
                    if _same_observation(g, H["FILTER"], H["PESTOROT"], H["RA"], H["DEC"]):
                        g["files"].append(f)
                        break
                else:
                    groups.append(
                        {"files": [f], "filter": H["FILTER"], "rotation": H["PESTOROT"], "ra": H["RA"], "dec": H["DEC"]}
                    )
            for g in groups:
                self.objects.append(ommobjFits(g["files"], self._reference, self._config, headers))
        self._associate_calibrations(night_dir)
        return self.objects

    def _calibration(self, folder: Path) -> list[tuple[Path, fits.Header]]:
        out = []
        for f in fits_files(folder) if folder.is_dir() else []:
            try:
                raw = fits.getheader(f)
                camera = get_camera(raw)
                if camera == "unknown":
                    raise ValueError("unknown camera")
                out.append(
                    (f, self._reference.standardize(raw, camera, filename=f, datatype="CALIB", derive_text=False, config=self._config))
                )
            except Exception as exc:  # noqa: BLE001
                log.warning("%s: ignored while scanning calibrations (%s)", f, exc)
        return out

    def _associate_calibrations(self, night_dir: Path) -> None:
        """Fill ``flat`` (DomeFlat: same filter and rotation) and ``dark`` (same exposure and readout mode)."""
        if not self.objects:
            return
        flats = self._calibration(night_dir / "DomeFlat")
        darks = self._calibration(night_dir / "Dark")
        for o in self.objects:
            o.flat = [
                f for f, h in flats if h["FILTER"] == o.filter and abs(h["PESTOROT"] - o.rotation) <= ROTATION_TOL_DEG
            ]
            o.dark = [
                f
                for f, h in darks
                if math.isclose(h["TEXP"], o.texp, rel_tol=EXPTIME_RTOL) and _mode_of(h) == o.mode
            ]


def _mode_of(h: fits.Header) -> dict[str, Any]:
    return {
        "amp_type": h["AMP_TYPE"],
        "h_freq": h["H_FREQ"],
        "v_freq": h["V_FREQ"],
        "ana_gain": h["ANA_GAIN"],
        "kgain": h["KGAIN_01"],
        "binning": (h["BIN_X"], h["BIN_Y"]),
        "crop": h["CRP_ENAB"],
        "overscan": h["OVS"],
    }


def level0_folders(night_dir: Path, only: Path | None = None) -> list[tuple[Path, str]]:
    """Folders to run level 0 on, with their DATATYPE.

    Target object folders are SCIENCE, calibration folders are CALIB. ``Video/``
    is never touched.
    """
    if only:
        return [(only, "SCIENCE")]
    out = [(night_dir / d, "CALIB") for d in CALIBRATION_DIRS if (night_dir / d).is_dir()]
    target = night_dir / "Target"
    if target.is_dir():
        out += [(p, "SCIENCE") for p in sorted(target.iterdir()) if p.is_dir()]
    return out


def resolve_dir(path: Path) -> tuple[Path, Path | None]:
    """Work out what the user pointed at.

    Returns ``(night_dir, only)`` where ``only`` is set when ``path`` is a single
    object folder (``<night>/Target/<object>``).

    Raises
    ------
    ValueError
        If ``path`` is neither a night folder nor an object folder.
    """
    path = path.resolve()
    if path.parent.name == "Target":
        return path.parent.parent, path
    if path.name == "Target":
        return path.parent, None
    if any((path / d).is_dir() for d in ("Target", *CALIBRATION_DIRS)):
        return path, None
    raise ValueError(f"{path} is not a night folder (no Target/, Dark/, DomeFlat/ or SkyFlat/) nor an object folder")
