"""Stage 7: Anomaly segmentation — connected components, morphological filtering."""
import numpy as np
from scipy import ndimage


def run(data: dict, params: dict) -> dict:
    """
    Input:  data["identifications"] — list of per-point identifications
            data["grid_shape"] — (rows, cols) of survey grid
            data["coordinates"] — list of {lat, lon} per point
    Output: data["anomalies"] — list of anomaly regions with properties
    """
    identifications = data.get("identifications", [])
    grid_shape = data.get("grid_shape", (50, 50))
    coordinates = data.get("coordinates", [])
    threshold = params.get("confidence_threshold", 0.5)
    min_area = params.get("min_anomaly_area", 3)

    # Build confidence maps per substance
    substance_ids = set()
    for ident in identifications:
        for sid in ident.get("scores", {}):
            substance_ids.add(sid)
        substance_ids.add(ident["substance_id"])

    anomalies = []
    for sid in substance_ids:
        conf_map = np.zeros(grid_shape)
        for i, ident in enumerate(identifications):
            row, col = divmod(i, grid_shape[1])
            if row < grid_shape[0]:
                score = ident.get("scores", {}).get(sid, ident.get("match_score", 0))
                conf_map[row, col] = score

        # Threshold and morphological cleanup
        binary = conf_map >= threshold
        binary = ndimage.binary_opening(binary, structure=np.ones((3, 3)))
        binary = ndimage.binary_closing(binary, structure=np.ones((3, 3)))

        # Connected component analysis
        labeled, n_components = ndimage.label(binary)

        for comp_id in range(1, n_components + 1):
            component_mask = labeled == comp_id
            area = int(np.sum(component_mask))
            if area < min_area:
                continue

            # Get centroid
            rows, cols = np.where(component_mask)
            center_row = int(np.mean(rows))
            center_col = int(np.mean(cols))
            center_idx = center_row * grid_shape[1] + center_col

            center_lat = coordinates[center_idx]["lat"] if center_idx < len(coordinates) else 0.0
            center_lon = coordinates[center_idx]["lon"] if center_idx < len(coordinates) else 0.0

            # Get depth info from identifications
            depth_info = {}
            for idx in range(len(identifications)):
                r, c = divmod(idx, grid_shape[1])
                if component_mask[r, c] and identifications[idx].get("substance_id") == sid:
                    depth_info = data.get("depths", [{}])[idx] if idx < len(data.get("depths", [])) else {}

            # Build polygon from component boundary
            boundary_points = []
            for r, c in zip(rows, cols):
                if coordinates and (r * grid_shape[1] + c) < len(coordinates):
                    boundary_points.append(coordinates[r * grid_shape[1] + c])

            anomaly = {
                "substance_id": sid,
                "center_lat": center_lat,
                "center_lon": center_lon,
                "depth_min": depth_info.get("depth", 0.0) - depth_info.get("uncertainty", 0.0),
                "depth_max": depth_info.get("depth", 0.0) + depth_info.get("uncertainty", 0.0),
                "depth_mean": depth_info.get("depth", 0.0),
                "confidence": float(np.max(conf_map[component_mask])),
                "area_pixels": area,
                "area_m2": area * data.get("pixel_area_m2", 100),
                "boundary": boundary_points[:20],
            }
            anomalies.append(anomaly)

    data["anomalies"] = anomalies
    return data
