"""Remote sensing alteration indices for mineral exploration.

Each mineral type has specific spectral indices computed from
Sentinel-2 / Landsat-8 / ASTER band ratios.
These serve as the first-tier surface anomaly filter in the
Yakymchuk three-tier detection system.
"""
import numpy as np


# Sentinel-2 band indices (0-based in typical band array order)
# B02=0(Blue), B03=1(Green), B04=2(Red), B08=3(NIR), B11=4(SWIR1), B12=5(SWIR2)
# For generic multi-band data: R, G, B, NIR, SWIR1, SWIR2


def compute_alteration_index(bands: dict, substance_id: str) -> float:
    """Compute substance-specific surface alteration index.

    Args:
        bands: dict with keys 'R', 'G', 'B', 'NIR', 'SWIR1', 'SWIR2', 'TIR'
               (values are float reflectance/dn)
        substance_id: target substance identifier

    Returns:
        Normalized alteration index (0.0 to 1.0)
    """
    R = bands.get('R', 0.0) + 1e-10
    G = bands.get('G', 0.0) + 1e-10
    B = bands.get('B', 0.0) + 1e-10
    NIR = bands.get('NIR', 0.0) + 1e-10
    SWIR1 = bands.get('SWIR1', 0.0) + 1e-10
    SWIR2 = bands.get('SWIR2', 0.0) + 1e-10
    TIR = bands.get('TIR', 0.0) + 1e-10

    index = _compute_raw_index(R, G, B, NIR, SWIR1, SWIR2, TIR, substance_id)
    return float(np.clip(index, 0.0, 1.0))


def compute_alteration_map(band_cube: np.ndarray, substance_id: str) -> np.ndarray:
    """Compute alteration index map for an entire image.

    Args:
        band_cube: (H, W, 6+) array with bands [R, G, B, NIR, SWIR1, SWIR2, ...]
        substance_id: target substance

    Returns:
        (H, W) normalized alteration index map
    """
    H, W = band_cube.shape[:2]
    result = np.zeros((H, W))

    R = band_cube[:, :, 0].astype(float) + 1e-10
    G = band_cube[:, :, 1].astype(float) + 1e-10
    B = band_cube[:, :, 2].astype(float) + 1e-10
    NIR = band_cube[:, :, 3].astype(float) if band_cube.shape[2] > 3 else R + 1e-10
    SWIR1 = band_cube[:, :, 4].astype(float) if band_cube.shape[2] > 4 else R + 1e-10
    SWIR2 = band_cube[:, :, 5].astype(float) if band_cube.shape[2] > 5 else R + 1e-10
    TIR = band_cube[:, :, 6].astype(float) if band_cube.shape[2] > 6 else R + 1e-10

    result = _compute_raw_index(R, G, B, NIR, SWIR1, SWIR2, TIR, substance_id)

    # Normalize to [0, 1]
    rmin, rmax = result.min(), result.max()
    if rmax > rmin:
        result = (result - rmin) / (rmax - rmin)
    else:
        result = np.zeros_like(result)

    return result


def get_alteration_description(substance_id: str) -> dict:
    """Return description of the alteration index for a substance."""
    descriptions = {
        "gold": {"name": "FeOx + AlOH 铁染指数", "bands": "R/G + SWIR2/SWIR1",
                 "target": "铁氧化物蚀变 + 粘土化"},
        "silver": {"name": "Al-OH + Fe-OH 银矿化指数", "bands": "SWIR2/SWIR1 × R/G",
                   "target": "银矿化带蚀变"},
        "copper": {"name": "斑岩铜铁染+绿泥石化指数", "bands": "(R/G)² + SWIR2/SWIR1",
                   "target": "斑岩铜矿系统"},
        "lead_zinc": {"name": "Pb-Zn 综合指数", "bands": "SWIR2/SWIR1 × R/G",
                      "target": "铅锌多金属矿化"},
        "iron": {"name": "铁矿化指数", "bands": "R/G + SWIR2/NIR",
                 "target": "铁氧化物富集"},
        "uranium": {"name": "赤铁矿化+硅化指数", "bands": "R/G + SWIR2/SWIR1",
                    "target": "铀矿蚀变"},
        "ree": {"name": "碳酸岩/碱性花岗岩指数", "bands": "SWIR2/SWIR1 × NIR/R",
                "target": "稀土矿化"},
        "lithium": {"name": "锂辉石伟晶岩指数", "bands": "SWIR2/SWIR1 + 高反照",
                    "target": "锂辉石伟晶岩"},
        "tungsten": {"name": "云英岩化指数", "bands": "SWIR2/SWIR1 + Fe-OH",
                     "target": "白钨矿/黑钨矿"},
        "tin": {"name": "锡石蚀变指数", "bands": "SWIR2/SWIR1 × R/G",
                "target": "锡石-硫化物矿床"},
        "oil": {"name": "烃微渗指数", "bands": "SWIR1/NIR × SWIR2/SWIR1",
                "target": "油气烃微渗"},
        "gas": {"name": "甲烷微渗指数", "bands": "SWIR2/SWIR1 + NIR/R",
                "target": "天然气微渗"},
        "hydrogen": {"name": "氢气地表异常指数", "bands": "SWIR2/NIR",
                     "target": "天然氢渗漏"},
        "coal": {"name": "煤矿指数", "bands": "SWIR2/NIR",
                 "target": "含煤地层"},
        "fluorite": {"name": "萤石指数", "bands": "SWIR2/SWIR1",
                     "target": "CaF₂ 矿化"},
        "water": {"name": "水体/含水层指数", "bands": "NIR/SWIR1 + B/NIR",
                  "target": "地下水/深层水"},
        "geothermal": {"name": "硅化+碳酸盐化指数", "bands": "SWIR2/SWIR1 + TIR",
                       "target": "高温地热田"},
    }
    return descriptions.get(substance_id, {"name": "通用蚀变指数", "bands": "N/A", "target": "N/A"})


def _compute_raw_index(R, G, B, NIR, SWIR1, SWIR2, TIR, substance_id: str):
    """Compute raw (unnormalized) alteration index."""
    index_funcs = {
        "gold": lambda: 0.5 * (R / G) + 0.5 * (SWIR2 / SWIR1),
        "silver": lambda: (SWIR2 / SWIR1) * (R / G),
        "copper": lambda: (R / G) ** 2 + (SWIR2 / SWIR1),
        "lead_zinc": lambda: (SWIR2 / SWIR1) * (R / G),
        "iron": lambda: (R / G) + (SWIR2 / NIR),
        "uranium": lambda: 0.5 * (R / G) + 0.5 * (SWIR2 / SWIR1),
        "ree": lambda: (SWIR2 / SWIR1) * (NIR / R),
        "lithium": lambda: 0.5 * (SWIR2 / SWIR1) + 0.3 * (NIR / R) + 0.2 * ((R + G + B) / 3 / NIR),
        "tungsten": lambda: 0.5 * (SWIR2 / SWIR1) + 0.3 * (R / G) + 0.2 * ((R + G + B) / 3 / NIR),
        "tin": lambda: (SWIR2 / SWIR1) * (R / G),
        "oil": lambda: (SWIR1 / NIR) * (SWIR2 / SWIR1),
        "gas": lambda: 0.5 * (SWIR2 / SWIR1) + 0.5 * (NIR / R),
        "hydrogen": lambda: SWIR2 / NIR,
        "coal": lambda: SWIR2 / NIR,
        "fluorite": lambda: SWIR2 / SWIR1,
        "water": lambda: 0.5 * (NIR / SWIR1) + 0.5 * (B / NIR),
        "geothermal": lambda: 0.5 * (SWIR2 / SWIR1) + 0.5 * TIR,
    }
    func = index_funcs.get(substance_id, lambda: R / G)
    return func()
