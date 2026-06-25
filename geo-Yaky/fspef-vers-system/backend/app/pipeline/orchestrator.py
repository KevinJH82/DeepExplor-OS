"""Pipeline orchestrator — chains 8 stages, tracks progress."""
import json
import numpy as np
from datetime import datetime
from typing import Callable

from . import (
    stage1_preprocessing, stage2_frequency_transform, stage3_feature_extraction,
    stage4_spectral_matching, stage5_material_identification, stage6_depth_calculation,
    stage7_anomaly_segmentation, stage8_3d_modeling,
)
from ..core.spectral_library import SpectralLibrary
from ..core.data_parser import parse_spectral_csv, parse_vers_csv, parse_geotiff_metadata
from ..core.satellite_frt import parse_satellite_bands, process_satellite_frt
from ..models.substance import Job, Anomaly, AnalysisResult


STAGES = [
    {"name": "preprocessing", "label": "预处理（去噪/校正）", "module": stage1_preprocessing},
    {"name": "frequency_transform", "label": "频域变换（FFT/CWT）", "module": stage2_frequency_transform},
    {"name": "feature_extraction", "label": "特征提取（峰值/Q因子）", "module": stage3_feature_extraction},
    {"name": "spectral_matching", "label": "光谱匹配（参考库对比）", "module": stage4_spectral_matching},
    {"name": "material_identification", "label": "物质识别（分类/置信度）", "module": stage5_material_identification},
    {"name": "depth_calculation", "label": "深度换算（H=C/2f）", "module": stage6_depth_calculation},
    {"name": "anomaly_segmentation", "label": "异常分割（连通域分析）", "module": stage7_anomaly_segmentation},
    {"name": "3d_modeling", "label": "3D建模（Kriging/等值面）", "module": stage8_3d_modeling},
]


def generate_demo_data(target_substances: list[str], grid_size: int = 50) -> dict:
    """Generate synthetic survey data with embedded anomalies for demo."""
    from ..db.seed import SUBSTANCE_CONFIGS

    np.random.seed(42)
    n_points = grid_size * grid_size
    fs = 50.0
    n_samples = 512
    lat_center, lon_center = 55.0, 73.0  # West Siberian Basin
    lat_span, lon_span = 0.05, 0.05  # ~5km

    coordinates = []
    raw_signals = []
    ground_truth = []

    configs = {c["id"]: c for c in SUBSTANCE_CONFIGS}

    # Define anomaly regions
    anomaly_defs = []
    for sid in target_substances:
        if sid in configs:
            cfg = configs[sid]
            anomaly_defs.append({
                "substance_id": sid,
                "center_row": np.random.randint(grid_size // 4, 3 * grid_size // 4),
                "center_col": np.random.randint(grid_size // 4, 3 * grid_size // 4),
                "radius": np.random.randint(5, 12),
                "peaks": cfg["peaks"],
            })

    for i in range(n_points):
        row, col = divmod(i, grid_size)
        lat = lat_center + (row / grid_size - 0.5) * lat_span
        lon = lon_center + (col / grid_size - 0.5) * lon_span
        coordinates.append({"lat": lat, "lon": lon})

        # Base signal: geological background noise
        t = np.arange(n_samples) / fs
        signal = np.random.randn(n_samples) * 0.3
        signal += 0.2 * np.sin(2 * np.pi * 0.5 * t)  # low-freq background

        # Add anomaly signal if within an anomaly region
        point_substance = None
        for adef in anomaly_defs:
            dist = np.sqrt((row - adef["center_row"]) ** 2 + (col - adef["center_col"]) ** 2)
            if dist < adef["radius"]:
                strength = 1.0 - dist / adef["radius"]
                for f0, amp, Q in adef["peaks"]:
                    bandwidth = f0 / Q
                    for f in np.linspace(max(0, f0 - 3 * bandwidth), f0 + 3 * bandwidth, 10):
                        signal += strength * amp * 0.5 * np.sin(2 * np.pi * f * t + np.random.uniform(0, 2 * np.pi))
                point_substance = adef["substance_id"]

        ground_truth.append(point_substance or "background")
        raw_signals.append(signal.tolist())

    return {
        "raw_signals": raw_signals,
        "fs": fs,
        "grid_shape": (grid_size, grid_size),
        "coordinates": coordinates,
        "pixel_area_m2": (lon_span * 111000 / grid_size) * (lat_span * 111000 / grid_size),
        "ground_truth": ground_truth,
    }


def load_real_data(upload_id: str | None, upload_dir: str) -> dict:
    """Load and parse real uploaded data into pipeline format."""
    from ..models.substance import Upload
    from ..db.session import SessionLocal

    if not upload_id:
        raise ValueError("真实模式需要上传数据文件")

    db = SessionLocal()
    upload = db.query(Upload).filter(Upload.id == upload_id).first()
    db.close()

    if not upload:
        raise ValueError(f"上传记录不存在: {upload_id}")

    file_path = upload.file_path
    file_type = upload.file_type

    if file_type == "csv":
        # Try spectral CSV first, fallback to VERS
        result = parse_spectral_csv(file_path)
        if result.get("error"):
            raise ValueError(f"CSV解析失败: {result['error']}")

        # Pad signals to uniform length
        max_len = max(len(s) for s in result["raw_signals"])
        padded = []
        for sig in result["raw_signals"]:
            arr = np.array(sig, dtype=float)
            if len(arr) < max_len:
                arr = np.pad(arr, (0, max_len - len(arr)))
            padded.append(arr.tolist())
        result["raw_signals"] = padded

        return result

    elif file_type in ("geotiff", "tif", "tiff"):
        # Try satellite FRT processing first for multi-band GeoTIFF
        sat_result = parse_satellite_bands(file_path)
        if sat_result and "band_cube" in sat_result:
            result = {
                "raw_signals": sat_result["band_cube"].reshape(-1, sat_result["band_cube"].shape[2]).tolist(),
                "fs": 50.0,
                "grid_shape": sat_result["grid_shape"],
                "coordinates": sat_result["coordinates"],
                "n_points": len(sat_result["coordinates"]),
                "n_bands": sat_result["n_bands"],
                "pixel_area_m2": 100.0,
                "satellite_frt": True,
                "band_cube": sat_result["band_cube"],
            }
            return result

        # Fallback to basic GeoTIFF parsing
        result = parse_geotiff_metadata(file_path)
        if result.get("error"):
            raise ValueError(f"GeoTIFF解析失败: {result['error']}")

        # Each band column becomes part of the signal
        max_len = max(len(s) for s in result["raw_signals"])
        padded = []
        for sig in result["raw_signals"]:
            arr = np.array(sig, dtype=float)
            if len(arr) < max_len:
                arr = np.pad(arr, (0, max_len - len(arr)))
            padded.append(arr.tolist())
        result["raw_signals"] = padded
        return result

    else:
        raise ValueError(f"不支持的文件格式: {file_type}")


def run_pipeline(data: dict, params: dict, spectral_lib: SpectralLibrary,
                 progress_callback: Callable | None = None) -> dict:
    """Execute the full 8-stage pipeline."""
    data["spectral_library"] = spectral_lib
    data["parameters"] = params

    for i, stage in enumerate(STAGES):
        if progress_callback:
            progress_callback(i + 1, stage["name"], (i / len(STAGES)) * 100, stage["label"])

        data = stage["module"].run(data, params)

    if progress_callback:
        progress_callback(8, "complete", 100.0, "分析完成")

    return data
