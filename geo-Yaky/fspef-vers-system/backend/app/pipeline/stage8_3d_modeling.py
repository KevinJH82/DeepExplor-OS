"""Stage 8: 3D modeling — isosurface extraction in normalized space."""
import numpy as np
import json
from ..core.mesh_generator import extract_isosurface, estimate_volume, generate_3d_scalar_field


def run(data: dict, params: dict) -> dict:
    """
    Input:  data["identifications"] — per-point identification results
            data["coordinates"] — list of {lat, lon}
            data["depths"] — list of depth info per point
    Output: data["models"] — dict of substance_id -> {mesh, volume, bounds}
    """
    identifications = data.get("identifications", [])
    coordinates = data.get("coordinates", [])
    depths = data.get("depths", [])
    grid_shape = data.get("grid_shape", (50, 50))
    isovalue = params.get("isovalue", 0.5)

    # Group by substance
    substance_points: dict[str, list] = {}
    for i, ident in enumerate(identifications):
        sid = ident["substance_id"]
        if sid not in substance_points:
            substance_points[sid] = []
        if i < len(coordinates) and i < len(depths):
            d = depths[i]
            substance_points[sid].append({
                "lat": coordinates[i]["lat"],
                "lon": coordinates[i]["lon"],
                "depth": d.get("depth", 0),
                "confidence": ident["confidence"],
            })

    NX, NY, NZ = 30, 30, 20

    models = {}
    for sid, points in substance_points.items():
        if len(points) < 4:
            continue

        pts = np.array([[p["lon"], p["lat"], p["depth"]] for p in points])
        vals = np.array([p["confidence"] for p in points])

        # Normalize each axis independently to [0, N] so the mesh is well-proportioned
        mins = pts.min(axis=0)
        spans = pts.max(axis=0) - mins
        spans[spans == 0] = 1.0  # avoid division by zero

        pts_norm = np.empty_like(pts)
        pts_norm[:, 0] = (pts[:, 0] - mins[0]) / spans[0] * NX
        pts_norm[:, 1] = (pts[:, 1] - mins[1]) / spans[1] * NY
        pts_norm[:, 2] = (pts[:, 2] - mins[2]) / spans[2] * NZ

        bounds_norm = [(-2, NX + 2), (-2, NY + 2), (-2, NZ + 2)]
        spacing = (1.0, 1.0, 1.0)

        volume_field = generate_3d_scalar_field(pts_norm, vals, grid_shape=(NX, NY, NZ), bounds=bounds_norm)
        mesh = extract_isosurface(volume_field, level=isovalue, spacing=spacing)
        vol = estimate_volume(volume_field, level=isovalue, spacing=spacing)

        models[sid] = {
            "mesh": mesh,
            "volume_m3": vol,
            "bounds": {
                "lon_min": float(mins[0]), "lon_max": float(mins[0] + spans[0]),
                "lat_min": float(mins[1]), "lat_max": float(mins[1] + spans[1]),
                "depth_min": float(mins[2]), "depth_max": float(mins[2] + spans[2]),
            },
            "n_points": len(points),
        }

    data["models"] = models
    return data
