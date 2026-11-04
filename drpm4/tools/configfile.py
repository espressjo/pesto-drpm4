"""Locations of the drpm4 runtime tree and access to the configuration."""

from __future__ import annotations

import configparser
import logging
import os
from importlib.resources import files
from pathlib import Path

log = logging.getLogger(__name__)

DEFAULT_HOME = "/opt/drpm4"
HEADER_CSV = "header.csv"
CONFIG_FILE = "pesto.cfg"


def drpm4_home() -> Path:
    """Return the runtime tree root (``$DRPM4_HOME`` or ``/opt/drpm4``)."""
    return Path(os.environ.get("DRPM4_HOME", DEFAULT_HOME))


def config_dir() -> Path:
    """Return the directory holding ``pesto.cfg``."""
    return drpm4_home() / "config"


def references_dir() -> Path:
    """Return the directory holding reference files such as ``header.csv``."""
    return drpm4_home() / "references"


def log_dir() -> Path:
    """Return the log directory."""
    return drpm4_home() / "log"


def _packaged(name: str) -> Path:
    return Path(str(files("drpm4").joinpath("resources", name)))


def header_csv_path() -> Path:
    """Return the header reference file.

    The installed copy in ``references/`` wins; if it is missing the copy
    shipped inside the package is used.
    """
    installed = references_dir() / HEADER_CSV
    if installed.is_file():
        return installed
    log.warning("%s not found, using the copy packaged with drpm4 (run install.sh)", installed)
    return _packaged(HEADER_CSV)


def load_config() -> configparser.ConfigParser:
    """Load the packaged defaults, then override with ``config/pesto.cfg``."""
    cfg = configparser.ConfigParser(inline_comment_prefixes=("#",))
    cfg.read(_packaged(CONFIG_FILE))
    installed = config_dir() / CONFIG_FILE
    if installed.is_file():
        cfg.read(installed)
    else:
        log.warning("%s not found, using packaged defaults (run install.sh)", installed)
    return cfg
