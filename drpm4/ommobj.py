"""Parse a night folder into observations (``ommobj``)."""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from pathlib import Path

from astropy.io import fits

from drpm4.pestoHeader import SENTINEL, HeaderReference, fits_files
from drpm4.tools.camera import get_camera
from drpm4.tools.configfile import load_config

log = logging.getLogger(__name__)

CALIBRATION_DIRS = ("Dark", "DomeFlat", "SkyFlat")
POSITION_TOL_ARCMIN = 4.0  # same name but farther apart than this is a different object
ROTATION_TOL_DEG = 0.01


@dataclass
class ommobj:
    """One observation: same object folder, filter, rotation and position."""

    path: Path
    object: str
    filter: str
    rotation: float
    ra: float  # hours, of the first file
    dec: float  # degrees, of the first file
    ls: list[Path] = field(default_factory=list)
    night: int = -999
    H: fits.Header | None = None  # standardized header of the first file

    @property
    def number_of_file(self) -> int:
        """Number of files in the observation."""
        return len(self.ls)

    def _increment(self, p: Path) -> int | None:
        try:
            return int(p.name.split("_")[1].split(".")[0])
        except (IndexError, ValueError):
            return None

    @property
    def start(self) -> int | None:
        """First increment."""
        return self._increment(self.ls[0])

    @property
    def stop(self) -> int | None:
        """Last increment."""
        return self._increment(self.ls[-1])


def _separation_arcmin(ra1: float, dec1: float, ra2: float, dec2: float) -> float:
    """Angular separation between two positions (RA in hours, DEC in degrees)."""
    a1, a2 = math.radians(ra1 * 15), math.radians(ra2 * 15)
    d1, d2 = math.radians(dec1), math.radians(dec2)
    cos_sep = math.sin(d1) * math.sin(d2) + math.cos(d1) * math.cos(d2) * math.cos(a1 - a2)
    return math.degrees(math.acos(max(-1.0, min(1.0, cos_sep)))) * 60


def _same_observation(o: ommobj, filt: str, rot: float, ra: float, dec: float) -> bool:
    if o.filter != filt or abs(o.rotation - rot) > ROTATION_TOL_DEG:
        return False
    if min(o.ra, ra, o.dec, dec) <= -999:  # position unknown: cannot split on it
        return True
    return _separation_arcmin(o.ra, o.dec, ra, dec) <= POSITION_TOL_ARCMIN


class ommobjs:
    """All the observations of a night."""

    def __init__(self, night_dir: Path, objects: list[ommobj]) -> None:
        self.night_dir = night_dir
        self.objects = objects

    @property
    def nb_objects(self) -> int:
        """Number of observations found."""
        return len(self.objects)

    @classmethod
    def parse(
        cls,
        night_dir: Path,
        only: Path | None = None,
        reference: HeaderReference | None = None,
    ) -> ommobjs:
        """Scan ``night_dir/Target`` and return its observations.

        Parameters
        ----------
        night_dir : Path
            Night folder (``yymmdd``).
        only : Path, optional
            Restrict to this single object folder.
        reference : HeaderReference, optional
            Header definition (loaded from the references folder if omitted).
        """
        reference = reference or HeaderReference.load()
        cfg = load_config()
        target = night_dir / "Target"
        folders = [only] if only else sorted(p for p in target.iterdir() if p.is_dir()) if target.is_dir() else []
        objects: list[ommobj] = []
        for folder in folders:
            found: list[ommobj] = []
            for f in fits_files(folder):
                try:
                    raw = fits.getheader(f)
                    camera = get_camera(raw)
                    if camera == "unknown":
                        raise ValueError("unknown camera")
                    H = reference.standardize(
                        raw, camera, filename=f, datatype="SCIENCE", derive_text=False, config=cfg
                    )
                except Exception as exc:  # noqa: BLE001
                    log.warning("%s: ignored while scanning (%s)", f, exc)
                    continue
                filt, rot = H["FILTER"], H["PESTOROT"]
                ra, dec = H["RA"], H["DEC"]
                for o in found:
                    if _same_observation(o, filt, rot, ra, dec):
                        o.ls.append(f)
                        break
                else:
                    found.append(
                        ommobj(folder, folder.name, filt, rot, ra, dec, ls=[f], night=H["NIGHTID"], H=H)
                    )
            objects.extend(found)
        return cls(night_dir, objects)


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
