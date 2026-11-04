"""Command line interface: ``drpm4 --dir /path/to/night``."""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date
from pathlib import Path

from drpm4 import __version__
from drpm4.ommobj import level0_folders, ommobjs, resolve_dir
from drpm4.pestoHeader import HeaderReference, run_level0
from drpm4.tools.configfile import load_config, log_dir

log = logging.getLogger("drpm4")


def _setup_logging(verbose: bool) -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    try:
        handlers.append(logging.FileHandler(log_dir() / f"drpm4-{date.today():%Y%m%d}.log"))
    except OSError:  # no runtime tree yet or read-only: console only
        pass
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        handlers=handlers,
    )


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="drpm4",
        description="PESTO data reduction. Currently runs level 0 (header standardization).",
    )
    p.add_argument("--dir", type=Path, default=Path.cwd(), metavar="PATH",
                   help="night folder or a single object folder (default: current directory)")
    p.add_argument("--list", action="store_true", help="list the objects, filters and rotations, then exit")
    p.add_argument("--skip-existing", action="store_true", help="do not rewrite files already in redux/")
    p.add_argument("-v", "--verbose", action="store_true", help="debug output")
    p.add_argument("--version", action="version", version=f"drpm4 {__version__}")
    return p


def main(argv: list[str] | None = None) -> int:
    """Entry point of the ``drpm4`` command. Return the process exit code."""
    args = _build_parser().parse_args(argv)
    _setup_logging(args.verbose)
    try:
        night_dir, only = resolve_dir(args.dir)
        reference = HeaderReference.load()
    except (ValueError, OSError) as exc:
        log.error("%s", exc)
        return 2
    log.info("night folder: %s%s", night_dir, f" (only {only.name})" if only else "")

    if args.list:
        objs = ommobjs.parse(night_dir, only=only, reference=reference)
        print(f"{'object':<24}{'filter':<10}{'rotation':>9}{'files':>7}  increments")
        for o in objs.objects:
            print(f"{o.object:<24}{o.filter:<10}{o.rotation:>9.2f}{o.number_of_file:>7}  {o.start}-{o.stop}")
        print(f"{objs.nb_objects} observation(s)")
        return 0

    config = load_config()
    failures = 0
    for folder, datatype in level0_folders(night_dir, only):
        res = run_level0(folder, datatype, reference, config, overwrite=not args.skip_existing)
        failures += len(res.failed)
        log.info("level 0 %-9s %-40s done=%d skipped=%d failed=%d",
                 datatype, folder.relative_to(night_dir) if not only else folder.name,
                 res.done, res.skipped, len(res.failed))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
