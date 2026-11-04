"""Level 0: standardize the headers of a PESTO data set.

The mapping from raw keywords to the standard header is data-driven, see
``resources/header.csv`` and section 2.3 of ``spec.md``. Originals are never
modified: standardized files are written to a ``redux/`` folder next to them.
"""

from __future__ import annotations

import csv
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any

import astropy.units as u
from astropy.coordinates import SkyCoord
from astropy.io import fits
from astropy.io.fits.card import Undefined

from drpm4.tools import pTime
from drpm4.tools.camera import get_camera
from drpm4.tools.configfile import header_csv_path, load_config

log = logging.getLogger(__name__)

SENTINEL: dict[str, Any] = {"INT": -999, "FLOAT": -999.99, "STR": "N/A", "BOOL": False}
CAMERAS = ("nuvu", "andor")
DRPCODE = "level-0"
_MISSING_FLOAT = -999.0  # anything at or below this is a "missing" marker
_warned: set[tuple[str, str, str]] = set()  # (camera, keyword, value) already reported


@dataclass(frozen=True)
class HeaderKey:
    """One row of ``header.csv``."""

    key: str
    type: str
    origin: str
    comments: str
    synonyms: tuple[str, ...]
    default: dict[str, str]
    transform: dict[str, str]


@dataclass
class Level0Result:
    """Outcome of a level 0 run on one folder."""

    folder: Path
    done: int = 0
    skipped: int = 0
    failed: list[tuple[Path, str]] = field(default_factory=list)


# --------------------------------------------------------------------------
# value helpers
# --------------------------------------------------------------------------
def _is_empty(value: Any) -> bool:
    return value is None or isinstance(value, Undefined) or (isinstance(value, str) and not value.strip())


def _cast(value: Any, typ: str) -> Any:
    """Convert ``value`` to the official type ``typ`` (INT, FLOAT, STR, BOOL)."""
    if typ == "INT":
        return int(float(value))
    if typ == "FLOAT":
        return float(value)
    if typ == "BOOL":
        if isinstance(value, str):
            return value.strip().lower() in ("t", "true", "1", "yes")
        return bool(value)
    return str(value).strip().strip("'").strip()


def _apply_transform(value: Any, spec: str) -> Any:
    """Apply one transform of the vocabulary described in spec.md. Return None for 'missing'."""
    if not spec:
        return value
    name, _, arg = spec.partition(":")
    name = name.strip()
    if name == "iso_time":
        return pTime.normalize_iso(str(value))
    if name == "scale":
        return float(value) * float(arg)
    if name == "missing_if":
        return None if float(value) == float(arg) else value
    if name == "auto_kelvin":
        return float(value) - 273.15 if float(value) > 50 else value
    if name == "map":
        for pair in arg.split(","):  # e.g. map:Positive>1,Negative>0
            src, _, dst = pair.partition(">")
            if str(value).strip() == src:
                return dst
        return value
    if name == "remove":
        return str(value).replace(arg, "").strip()
    if name == "to_bool":
        return _cast(value, "BOOL")
    raise ValueError(f"unknown transform {spec!r}")


# --------------------------------------------------------------------------
# reference file
# --------------------------------------------------------------------------
class HeaderReference:
    """The standard header definition, loaded from ``header.csv``."""

    def __init__(self, keys: list[HeaderKey]) -> None:
        self.keys = keys

    @classmethod
    def load(cls, path: Path | str | None = None) -> HeaderReference:
        """Load the reference (default: the installed ``references/header.csv``)."""
        path = Path(path) if path else header_csv_path()
        keys: list[HeaderKey] = []
        with open(path, newline="", encoding="utf-8-sig") as fh:
            for row in csv.DictReader(fh, delimiter=";"):
                keys.append(
                    HeaderKey(
                        key=row["key"].strip(),
                        type=row["type"].strip(),
                        origin=row["origin"].strip(),
                        comments=row["comments"].strip(),
                        synonyms=tuple(s.strip() for s in row["synonyms"].split("|") if s.strip()),
                        default={c: row[f"default_{c}"] for c in CAMERAS},
                        transform={c: row[f"transform_{c}"].strip() for c in CAMERAS},
                    )
                )
        log.debug("loaded %d header keys from %s", len(keys), path)
        return cls(keys)

    # ----------------------------------------------------------------------
    def standardize(
        self,
        raw: fits.Header,
        camera: str,
        *,
        filename: Path | str = "",
        datatype: str = "N/A",
        derive_text: bool = True,
        config=None,
    ) -> fits.Header:
        """Convert a raw header into the standard header.

        Parameters
        ----------
        raw : astropy.io.fits.Header
            Header as written by the acquisition system.
        camera : {"nuvu", "andor"}
            Detector that produced the file, see ``tools.camera.get_camera``.
        filename : Path or str
            Original file name (the Andor beta software stores the object name there).
        datatype : str
            Value of DATATYPE (SCIENCE, CALIB, ...).
        derive_text : bool
            Compute RATXT/DECTXT. Switch off when only grouping files (it is slow).
        config : configparser.ConfigParser, optional
            Configuration, loaded from ``pesto.cfg`` when omitted.

        Returns
        -------
        astropy.io.fits.Header
            Standard header without the structural keywords (astropy writes those).
        """
        if camera not in CAMERAS:
            raise ValueError(f"unsupported camera {camera!r}")
        cfg = config or load_config()
        ctx = _Context(raw=raw, camera=camera, filename=Path(filename), cfg=cfg)
        values: dict[str, Any] = {}
        deferred: list[HeaderKey] = []

        for k in self.keys:
            if k.origin in ("structural", "derived"):
                continue
            if k.origin == "pipeline":
                values[k.key] = DRPCODE if k.key == "DRPCODE" else datatype
                continue
            default = k.default[camera].strip()
            value = None
            if k.origin == "raw":
                value = self._from_raw(k, ctx)
                if k.key == "CTRLTIME":
                    ctx.ctrltime_in_raw = value is not None
            if value is None:
                if default[:1] in ("@", "="):
                    deferred.append(k)
                    continue
                value = _cast(default, k.type) if default else SENTINEL[k.type]
            values[k.key] = value

        # '@KEY' copies another standard key, '=name' calls a derivation function.
        for k in sorted(deferred, key=lambda k: k.default[camera].strip()[0] != "@"):
            default = k.default[camera].strip()
            if default[0] == "@":
                value = values.get(default[1:])
            else:
                value = _DERIVATIONS[default[1:]](ctx, values)
            values[k.key] = SENTINEL[k.type] if value is None else _cast(value, k.type)

        self._derive(values, ctx, derive_text)

        header = fits.Header()
        for k in self.keys:
            if k.key in values:
                header[k.key] = (values[k.key], k.comments)
        return header

    # ----------------------------------------------------------------------
    @staticmethod
    def _from_raw(k: HeaderKey, ctx: _Context) -> Any:
        """Return the cleaned, typed raw value of ``k`` or None if missing/unusable."""
        for name in (k.key, *k.synonyms):
            raw_value = ctx.raw.get(name)
            if _is_empty(raw_value):
                continue
            try:
                value = _apply_transform(raw_value, k.transform[ctx.camera])
                return None if value is None else _cast(value, k.type)
            except (ValueError, TypeError) as exc:
                # The same bad value shows up in every file of a night: say it once.
                first = (ctx.camera, name, repr(raw_value)) not in _warned
                _warned.add((ctx.camera, name, repr(raw_value)))
                log.log(
                    logging.WARNING if first else logging.DEBUG,
                    "%s: cannot use %s=%r (%s), using default%s",
                    ctx.filename.name, name, raw_value, exc, " (reported once, same for the other files)" if first else "",
                )
        return None

    def _derive(self, values: dict[str, Any], ctx: _Context, derive_text: bool) -> None:
        """Compute NIGHTID, RELEASE, RATXT, DECTXT and GPS."""
        wanted = {k.key: k for k in self.keys if k.origin == "derived"}
        utc = pTime.parse_utc(values.get("CTRLTIME", ""))
        if "NIGHTID" in wanted or "RELEASE" in wanted:
            if utc is None:
                log.warning("%s: no usable CTRLTIME, NIGHTID/RELEASE unknown", ctx.filename.name)
                night = None
            else:
                night = pTime.night_date(utc, ctx.cfg["site"]["timezone"])
            values["NIGHTID"] = int(night.strftime("%y%m%d")) if night else SENTINEL["INT"]
            days = ctx.cfg.getint("level0", "release_days")
            values["RELEASE"] = (
                (night + timedelta(days=days)).strftime("%Y-%m-%d") if night else SENTINEL["STR"]
            )
        if derive_text and "RATXT" in wanted:
            ra, dec = values.get("RA", SENTINEL["FLOAT"]), values.get("DEC", SENTINEL["FLOAT"])
            if ra <= _MISSING_FLOAT or dec <= _MISSING_FLOAT:
                values["RATXT"] = values["DECTXT"] = "UNKNOWN"
            else:
                text = SkyCoord(ra * u.hourangle, dec * u.deg).to_string("hmsdms", precision=3)
                values["RATXT"], values["DECTXT"] = text.split(" ")
        elif "RATXT" in wanted:
            values["RATXT"] = values["DECTXT"] = SENTINEL["STR"]
        if "GPS" in wanted:
            if ctx.camera == "nuvu":
                values["GPS"] = ctx.ctrltime_in_raw
            else:
                k = wanted["GPS"]
                raw_value = next((ctx.raw.get(n) for n in (k.key, *k.synonyms) if not _is_empty(ctx.raw.get(n))), None)
                values["GPS"] = False if raw_value is None else _apply_transform(raw_value, k.transform["andor"])


# --------------------------------------------------------------------------
# derivation functions referenced by '=name' defaults in header.csv
# --------------------------------------------------------------------------
@dataclass
class _Context:
    raw: fits.Header
    camera: str
    filename: Path
    cfg: Any
    ctrltime_in_raw: bool = False


def _utc(values: dict[str, Any]):
    return pTime.parse_utc(values.get("CTRLTIME", ""))


def _from_filename(ctx: _Context, values: dict[str, Any]) -> str | None:
    """Object name stored in the file name (Andor 1.0.0-beta: ``<object>_<increment>.fits``)."""
    stem = re.sub(r"\.fits(\.gz)?$", "", ctx.filename.name)
    if "_" not in stem:
        return None
    name = "_".join(stem.split("_")[:-1])
    return None if not name or name.isdigit() else name  # digits = already a night name


def _sidereal_time(ctx: _Context, values: dict[str, Any]) -> float | None:
    utc = _utc(values)
    site = ctx.cfg["site"]
    return None if utc is None else pTime.sidereal_hours(
        utc, site.getfloat("latitude"), site.getfloat("longitude"), site.getfloat("height")
    )


def _ut_hours(ctx: _Context, values: dict[str, Any]) -> float | None:
    utc = _utc(values)
    return None if utc is None else pTime.ut_hours(utc)


def _year(ctx: _Context, values: dict[str, Any]) -> float | None:
    utc = _utc(values)
    return None if utc is None else float(utc.year)


def _img_size(ctx: _Context, values: dict[str, Any]) -> str | None:
    n1, n2 = ctx.raw.get("NAXIS1"), ctx.raw.get("NAXIS2")
    return None if n1 is None or n2 is None else f"{n1},{n2}"


_DERIVATIONS: dict[str, Callable[[_Context, dict[str, Any]], Any]] = {
    "from_filename": _from_filename,
    "sidereal_time": _sidereal_time,
    "ut_hours": _ut_hours,
    "year": _year,
    "img_size": _img_size,
}


# --------------------------------------------------------------------------
# running level 0
# --------------------------------------------------------------------------
def fits_files(folder: Path) -> list[Path]:
    """Return the FITS files directly inside ``folder`` in natural order (no recursion)."""
    files = [p for p in folder.iterdir() if p.is_file() and re.search(r"\.fits(\.gz)?$", p.name)]
    return sorted(files, key=lambda p: [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", p.name)])


def _output_name(src: Path, header: fits.Header, camera: str) -> str:
    """Andor 1.0.0-beta stores the object name in the file name: swap it for the night name."""
    if camera != "andor" or "1.0.0-beta" not in header["ACQSOFT"] or header["NIGHTID"] < 0:
        return src.name
    stem = re.sub(r"\.fits(\.gz)?$", "", src.name)
    if "_" not in stem:
        return src.name
    obj = "_".join(stem.split("_")[:-1])
    return src.name if obj.isdigit() else src.name.replace(obj, str(header["NIGHTID"]), 1)


def level0_file(
    src: Path, redux: Path, reference: HeaderReference, datatype: str, config=None, overwrite: bool = True
) -> Path | None:
    """Standardize one file into ``redux``. Return the output path, None if skipped."""
    with fits.open(src) as hdul:
        raw, data = hdul[0].header, hdul[0].data
        camera = get_camera(raw)
        if camera == "unknown":
            raise ValueError("cannot tell which camera wrote this file")
        header = reference.standardize(raw, camera, filename=src, datatype=datatype, config=config)
        dst = redux / _output_name(src, header, camera)
        if dst.exists() and not overwrite:
            return None
        fits.PrimaryHDU(data=data, header=header).writeto(dst, overwrite=True)
    return dst


def run_level0(
    folder: Path,
    datatype: str,
    reference: HeaderReference | None = None,
    config=None,
    overwrite: bool = True,
) -> Level0Result:
    """Level 0 on every FITS file of ``folder``; results go to ``folder/redux``.

    Level 0 must not fail: a bad file is logged and counted, the run goes on.
    """
    reference = reference or HeaderReference.load()
    config = config or load_config()
    result = Level0Result(folder=folder)
    files = fits_files(folder)
    if not files:
        return result
    redux = folder / "redux"
    redux.mkdir(exist_ok=True)
    for src in files:
        try:
            out = level0_file(src, redux, reference, datatype, config, overwrite)
        except Exception as exc:  # noqa: BLE001 - level 0 may never abort the night
            log.error("%s: level 0 failed: %s", src, exc)
            result.failed.append((src, str(exc)))
            continue
        if out is None:
            result.skipped += 1
        else:
            result.done += 1
    return result
