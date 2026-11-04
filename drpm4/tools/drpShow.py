"""Display images (zscale, ds9 like)."""

from __future__ import annotations

import numpy as np


def show_image(data: np.ndarray, title: str = "") -> None:
    """Show one image with a zscale stretch and the origin at the bottom left.

    Parameters
    ----------
    data : numpy.ndarray
        2D image.
    title : str
        Figure title.
    """
    import matplotlib.pyplot as plt
    from astropy.visualization import ZScaleInterval

    vmin, vmax = ZScaleInterval().get_limits(data)
    fig, ax = plt.subplots()
    ax.imshow(data, origin="lower", cmap="gray", vmin=vmin, vmax=vmax)
    ax.set_title(title)
    plt.show()
