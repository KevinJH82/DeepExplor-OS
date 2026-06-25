"""Kriging interpolation wrapper using PyKrige."""
import numpy as np
from pykrige.ok import OrdinaryKriging


def kriging_interpolate_2d(x: np.ndarray, y: np.ndarray, z: np.ndarray,
                           grid_x: np.ndarray, grid_y: np.ndarray,
                           variogram_model: str = "gaussian") -> tuple[np.ndarray, np.ndarray]:
    """2D Ordinary Kriging interpolation. Returns (interpolated_grid, variance_grid)."""
    ok = OrdinaryKriging(x, y, z, variogram_model=variogram_model, verbose=False, enable_plotting=False)
    z_grid, ss_grid = ok.execute("grid", grid_x, grid_y)
    return z_grid, ss_grid


def kriging_interpolate_points(x: np.ndarray, y: np.ndarray, z: np.ndarray,
                               target_x: np.ndarray, target_y: np.ndarray,
                               variogram_model: str = "gaussian") -> np.ndarray:
    """Kriging interpolation at specific target points."""
    ok = OrdinaryKriging(x, y, z, variogram_model=variogram_model, verbose=False, enable_plotting=False)
    z_pred, _ = ok.execute("points", target_x, target_y)
    return z_pred
