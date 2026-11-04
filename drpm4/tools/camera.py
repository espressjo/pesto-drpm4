"""Find out which detector produced a FITS file."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from astropy.io import fits

Camera = Literal["nuvu", "andor", "unknown"]


def get_camera(source: Path | str | fits.Header) -> Camera:
    """Return the detector that wrote a header.

    Parameters
    ----------
    source : Path, str or astropy.io.fits.Header
        A FITS file or an already-read raw header.

    Returns
    -------
    {"nuvu", "andor", "unknown"}
    """
    header = source if isinstance(source, fits.Header) else fits.getheader(source)
    if header.get("DET_TYPE") == "CCD201-20":  # old Nuvu acquisition (4.1.1)
        return "nuvu"
    if all(k in header for k in ("FIRM_VER", "HARD_VER", "FPGA_VER")):
        return "nuvu"
    if all(k in header for k in ("MIPS-LO", "MIPS-HI")):
        return "andor"
    if "libandor" in str(header.get("CAMERA", "")).lower():
        return "andor"
    return "unknown"
