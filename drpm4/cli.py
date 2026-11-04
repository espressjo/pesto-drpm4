"""Command line interface: ``drpm4 --dir /path/to/night``."""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date
from pathlib import Path

from drpm4 import __version__
from drpm4.archive import archive_night
from drpm4.nightReport import NightReport
from drpm4.ommobj import level0_folders, ommobjs, resolve_dir
from drpm4.pestoAstrometry import AstrometrySolver, run_level1
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
    if not verbose:  # the astrometry.net extension logs every quad it tries
        for h in handlers:
            h.addFilter(lambda r: not r.getMessage().startswith("solve "))


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="drpm4",
        description="PESTO data reduction. Runs level 0 (headers), level 1 (astrometry), then builds the night report.",
    )
    p.add_argument("--dir", type=Path, default=Path.cwd(), metavar="PATH",
                   help="night folder or a single object folder (default: current directory)")
    p.add_argument("--list", action="store_true", help="list the objects, filters and rotations, then exit")
    p.add_argument("--skip-existing", action="store_true", help="do not rewrite files already in redux/")
    p.add_argument("--level", type=int, choices=(0, 1), default=1,
                   help="highest reduction level to run: 0 = headers, 1 = headers + astrometry (default)")
    p.add_argument("--archive", action="store_true",
                   help="after the other steps: empty Video/ and gzip every FITS file (never done unless asked)")
    p.add_argument("--archive-only", action="store_true",
                   help="only archive (empty Video/, gzip every FITS file), no level 0/1 and no report")
    p.add_argument("--dry-run", action="store_true", help="with --archive/--archive-only: show what would be done")
    p.add_argument("--no-report", action="store_true", help="run level 0 only, no night report")
    p.add_argument("--report-only", action="store_true", help="build the night report only, no level 0")
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
        objs = ommobjs(reference)
        objs.parse(night_dir, only=only)
        print(f"{'object':<24}{'filter':<10}{'rotation':>9}{'files':>7}{'flat':>6}{'dark':>6}  increments")
        for o in objs:
            print(f"{o.object:<24}{o.filter:<10}{o.rotation:>9.2f}{o.number_of_file:>7}{len(o.flat):>6}{len(o.dark):>6}  {o.start}-{o.stop}")
        print(f"{objs.nb_objects} observation(s), {objs.observing_time:.2f} h of exposure")
        return 0

    if args.archive_only:
        return 1 if _archive(night_dir, only, args.dry_run) else 0

    config = load_config()
    failures = 0
    for folder, datatype in [] if args.report_only else level0_folders(night_dir, only):
        res = run_level0(folder, datatype, reference, config, overwrite=not args.skip_existing)
        failures += len(res.failed)
        log.info("level 0 %-9s %-40s done=%d skipped=%d failed=%d",
                 datatype, folder.relative_to(night_dir) if not only else folder.name,
                 res.done, res.skipped, len(res.failed))
    if args.level >= 1 and not args.report_only:
        targets = [f for f, kind in level0_folders(night_dir, only) if kind == "SCIENCE"]
        try:
            solver = AstrometrySolver(config)
        except (FileNotFoundError, ImportError) as exc:
            log.error("level 1 not run: %s", exc)
            failures += 1
        else:
            for folder in targets:
                res = run_level1(folder, solver, config, overwrite=not args.skip_existing)
                failures += len(res.failed)
                log.info("level 1 %-40s solved=%d tracked=%d unsolved=%d skipped=%d failed=%d",
                         folder.relative_to(night_dir) if not only else folder.name,
                         res.solved, res.tracked, len(res.unsolved), res.skipped, len(res.failed))

    if not args.no_report:
        objs = ommobjs(reference, config)
        objs.parse(night_dir, only=only)
        report = NightReport(objs, config).build()
        if report is None:
            failures += 1
    if args.archive:
        if failures:
            log.error("not archiving: %d error(s) above. Fix them, then run: drpm4 --archive-only", failures)
        else:
            failures += _archive(night_dir, only, args.dry_run)
    return 1 if failures else 0


def _archive(night_dir: Path, only: Path | None, dry_run: bool) -> int:
    """Run the archive step and log what it did. Return the number of failures."""
    res = archive_night(night_dir, only=only, dry_run=dry_run)
    verb = "would be" if dry_run else "were"
    log.info("archive: %d FITS files %s gzipped (%.1f MB -> %s)", res.gzipped, verb, res.bytes_before / 1e6,
             "?" if dry_run else f"{res.bytes_after / 1e6:.1f} MB")
    if only is None:
        log.info("archive: %d file(s) %s removed from Video/ (%.1f MB)", res.video_files, verb, res.video_bytes / 1e6)
    return len(res.failed)


if __name__ == "__main__":
    sys.exit(main())
