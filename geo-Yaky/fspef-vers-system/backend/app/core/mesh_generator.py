"""Marching cubes isosurface extraction for 3D geological modeling."""
import numpy as np
from skimage.measure import marching_cubes


def extract_isosurface(volume: np.ndarray, level: float = 0.5,
                       spacing: tuple[float, float, float] = (1.0, 1.0, 1.0)) -> dict:
    """Extract isosurface mesh from 3D scalar field using marching cubes."""
    try:
        verts, faces, normals, values = marching_cubes(volume, level=level, spacing=spacing)
        return {
            "vertices": verts.tolist(),
            "faces": faces.tolist(),
            "normals": normals.tolist(),
            "values": values.tolist(),
            "n_vertices": len(verts),
            "n_faces": len(faces),
        }
    except (ValueError, RuntimeError):
        return {"vertices": [], "faces": [], "normals": [], "values": [], "n_vertices": 0, "n_faces": 0}


def estimate_volume(volume: np.ndarray, level: float = 0.5,
                    spacing: tuple[float, float, float] = (1.0, 1.0, 1.0)) -> float:
    """Estimate volume of region above threshold in the scalar field."""
    voxel_volume = spacing[0] * spacing[1] * spacing[2]
    return float(np.sum(volume >= level) * voxel_volume)


def generate_3d_scalar_field(points: np.ndarray, values: np.ndarray,
                              grid_shape: tuple[int, int, int] = (50, 50, 30),
                              bounds: tuple | None = None) -> np.ndarray:
    """Generate 3D scalar field from scattered point data using inverse distance weighting."""
    if bounds is None:
        x_min, x_max = points[:, 0].min(), points[:, 0].max()
        y_min, y_max = points[:, 1].min(), points[:, 1].max()
        z_min, z_max = points[:, 2].min(), points[:, 2].max()
    else:
        (x_min, x_max), (y_min, y_max), (z_min, z_max) = bounds

    x_grid = np.linspace(x_min, x_max, grid_shape[0])
    y_grid = np.linspace(y_min, y_max, grid_shape[1])
    z_grid = np.linspace(z_min, z_max, grid_shape[2])

    X, Y, Z = np.meshgrid(x_grid, y_grid, z_grid, indexing="ij")
    grid_points = np.column_stack([X.ravel(), Y.ravel(), Z.ravel()])

    # Inverse distance weighting
    power = 2.0
    result = np.zeros(len(grid_points))
    for i, gp in enumerate(grid_points):
        distances = np.linalg.norm(points - gp, axis=1)
        distances = np.maximum(distances, 1e-10)
        weights = 1.0 / distances ** power
        result[i] = np.sum(weights * values) / np.sum(weights)

    return result.reshape(grid_shape)
