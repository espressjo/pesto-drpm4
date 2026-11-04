"""Archive a night: empty ``Video/`` and gzip every FITS file individually.

Never run automatically, only with ``drpm4 --archive`` or ``--archive-only``.
A FITS file is replaced by its ``.fits.gz`` only after the compressed copy was read
back and found to hold exactly the same number of bytes (the gzip CRC is checked while reading).
"""

from __future__ import annotations

import gzip
import logging
import os
import shutil
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

VIDEO_DIR = "Video"
CHUNK = 1 << 20


@dataclass
class ArchiveResult:
    """What an archive run did (or would do with ``dry_run``)."""

    gzipped: int = 0
    bytes_before: int = 0
    bytes_after: int = 0
    video_files: int = 0
    video_bytes: int = 0
    failed: list[tuple[Path, str]] = field(default_factory=list)

    @property
    def saved(self) -> int:
        """Bytes saved by the compression."""
        return self.bytes_before - self.bytes_after


def _gz_size(path: Path) -> int:
    """Return the uncompressed size of a gzip file, reading it all (so the CRC is verified)."""
    total = 0
    with gzip.open(path, "rb") as fh:
        while chunk := fh.read(CHUNK):
            total += len(chunk)
    return total


def gzip_file(path: Path) -> tuple[int, int]:
    """Replace ``path`` by ``path + '.gz'`` and return (size before, size after).

    The original is deleted only once the compressed file has been verified. If a
    complete ``.gz`` is already there (an interrupted earlier run) the original is just removed.

    Raises
    ------
    OSError, EOFError
        If the compressed copy is not valid; the original is left untouched.
    """
    dest = path.with_name(path.name + ".gz")
    size = path.stat().st_size
    if dest.exists():
        try:
            if _gz_size(dest) == size:
                path.unlink()
                return size, dest.stat().st_size
        except (OSError, EOFError):
            pass
        raise FileExistsError(f"{dest.name} exists and is not a copy of {path.name}")
    part = path.with_name(path.name + ".gz.part")
    try:
        with open(path, "rb") as src, open(part, "wb") as raw, \
                gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=0) as out:
            while chunk := src.read(CHUNK):
                out.write(chunk)
        if _gz_size(part) != size:
            raise OSError("the compressed copy does not match the original")
        shutil.copystat(path, part)
        os.replace(part, dest)
    finally:
        part.unlink(missing_ok=True)
    path.unlink()
    return size, dest.stat().st_size


def clean_video(night_dir: Path, dry_run: bool = False) -> tuple[int, int]:
    """Delete everything inside ``Video/`` (the folder itself stays). Return (files, bytes)."""
    video = night_dir / VIDEO_DIR
    if not video.is_dir():
        return 0, 0
    files = [p for p in video.rglob("*") if p.is_file() or p.is_symlink()]
    size = sum(p.lstat().st_size for p in files)
    if not dry_run:
        for child in video.iterdir():
            shutil.rmtree(child) if child.is_dir() and not child.is_symlink() else child.unlink()
    return len(files), size


def fits_to_archive(root: Path) -> list[Path]:
    """Return every ``.fits`` under ``root`` (redux/ included, Video/ excluded)."""
    return sorted(
        p for p in root.rglob("*.fits")
        if p.is_file() and VIDEO_DIR not in p.relative_to(root).parts and not p.name.startswith("tmp-")
    )


def archive_night(night_dir: Path, only: Path | None = None, dry_run: bool = False) -> ArchiveResult:
    """Archive a night.

    Parameters
    ----------
    night_dir : Path
        Night folder.
    only : Path, optional
        A single object folder: only its FITS files are gzipped and ``Video/`` is left alone.
    dry_run : bool
        Count what would be done without changing anything.
    """
    result = ArchiveResult()
    if only is None:
        result.video_files, result.video_bytes = clean_video(night_dir, dry_run)
    files = fits_to_archive(only or night_dir)

    def work(path: Path) -> tuple[Path, int, int, str]:
        if dry_run:
            return path, path.stat().st_size, 0, ""
        try:
            before, after = gzip_file(path)
            return path, before, after, ""
        except Exception as exc:  # noqa: BLE001 - one bad file must not stop the archive
            return path, 0, 0, str(exc)

    with ThreadPoolExecutor(max_workers=min(8, os.cpu_count() or 1)) as pool:
        for path, before, after, error in pool.map(work, files):
            if error:
                log.error("%s: not archived: %s", path, error)
                result.failed.append((path, error))
                continue
            result.gzipped += 1
            result.bytes_before += before
            result.bytes_after += after
    return result
