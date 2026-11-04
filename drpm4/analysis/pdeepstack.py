"""Deep stack of an observation: the frames aligned and combined."""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
from scipy.ndimage import shift as nd_shift

from drpm4.analysis._common import read_frame, sample_indices
from drpm4.ommobj import ommobj
from drpm4.tools.astroArray import register_offset

log = logging.getLogger(__name__)


class deepstack:
    """Median stack of frames spread over the observation, aligned by phase correlation.

    Parameters
    ----------
    obj : ommobj
        The observation.
    nb : int
        Number of frames stacked.
    """

    def __init__(self, obj: ommobj, nb: int = 30) -> None:
        self.obj = obj
        self.nb = nb
        self._stack: np.ndarray | None = None
        self._nb_used = 0

    def stack(self, nb: int | None = None) -> np.ndarray:
        """Return the stacked image (NaN-aware median of the aligned frames)."""
        nb = nb or self.nb
        if self._stack is not None and nb == self.nb:
            return self._stack
        ref, frames = None, []
        for i in sample_indices(self.obj.number_of_file, nb):
            try:
                frame = read_frame(self.obj, i)
                if ref is None:
                    ref = frame
                    frames.append(frame)
                else:
                    dx, dy = register_offset(ref, frame)
                    frames.append(nd_shift(frame, (-dy, -dx), order=1, mode="constant", cval=np.nan))
            except Exception as exc:  # noqa: BLE001
                log.warning("%s: skipped in the stack (%s)", self.obj.ls[i].name, exc)
        if not frames:
            raise ValueError(f"{self.obj.object}: no frame could be stacked")
        out = np.nanmedian(np.array(frames), axis=0)
        if nb == self.nb:
            self._stack, self._nb_used = out, len(frames)
        return out

    def _draw(self, ax, nb: int | None = None) -> None:
        from astropy.visualization import ImageNormalize, LinearStretch, ZScaleInterval

        img = self.stack(nb)
        ax.imshow(img, norm=ImageNormalize(img[np.isfinite(img)], interval=ZScaleInterval(), stretch=LinearStretch()),
                  cmap="gray", origin="lower")
        ax.set_title(f"Deep stack: {self.obj.object} ({self.obj.filter}), {self._nb_used or nb} frames")
        ax.set_xticks([])
        ax.set_yticks([])

    def create_deepstack_png(self, path: Path | str) -> Path:
        """Save the deep stack as a PNG and return its path."""
        from matplotlib.figure import Figure

        fig = Figure(figsize=(6, 4), layout="constrained")
        self._draw(fig.subplots())
        fig.savefig(path, dpi=150)
        return Path(path)

    def show_deepstack(self, nb: int = 30) -> None:
        """Show a stack of ``nb`` frames (zscale, ds9 like)."""
        import matplotlib.pyplot as plt

        _, ax = plt.subplots(layout="constrained")
        self._draw(ax, nb)
        plt.show()
