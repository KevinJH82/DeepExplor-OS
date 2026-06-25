"""Stage 6: Depth calculation using H = C/(2f) formula."""
from ..core.depth_estimator import calculate_depth, aggregate_depths

SUBSTANCE_C_EQUIVALENT = {
    "gold": 350.0, "silver": 330.0, "copper": 320.0, "lead_zinc": 310.0,
    "iron": 340.0, "uranium": 300.0, "ree": 290.0, "lithium": 280.0,
    "tungsten": 360.0, "tin": 340.0,
    "oil": 300.0, "gas": 250.0, "hydrogen": 200.0, "coal": 260.0,
    "fluorite": 270.0, "water": 280.0, "geothermal": 250.0,
}


def run(data: dict, params: dict) -> dict:
    """
    Input:  data["identifications"] — list of {substance_id, confidence, ...}
            data["features"] — list of {peaks: [{frequency, amplitude, q_factor}]}
    Output: data["depths"] — list of {substance_id, depth, uncertainty, ...}
    """
    identifications = data.get("identifications", [])
    features = data.get("features", [])
    c_override = params.get("c_equivalent")

    depths = []
    for i, ident in enumerate(identifications):
        sid = ident["substance_id"]
        c_eq = c_override if c_override else SUBSTANCE_C_EQUIVALENT.get(sid, 300.0)

        feat = features[i] if i < len(features) else {"peaks": []}
        peaks = feat.get("peaks", [])

        depth_info = aggregate_depths(peaks, c_eq, weights="energy")
        depth_info["substance_id"] = sid
        depth_info["confidence"] = ident["confidence"]
        depths.append(depth_info)

    data["depths"] = depths
    return data
