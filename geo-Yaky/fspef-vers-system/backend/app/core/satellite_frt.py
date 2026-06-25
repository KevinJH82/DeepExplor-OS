"""Satellite image Frequency Resonance Treatment (FRT) processing.

Core of Yakymchuk's method: extract pixel gray-level sequences from
multi-band satellite imagery (Sentinel-2, Landsat-8, ASTER),
perform FFT on each pixel's spectral profile, and detect:
  1. 470km young granite channel (f ≈ 3.70 Hz)
  2. 996km old channel (f = 7.83 Hz)
  3. Target substance characteristic frequency (f ≈ 21.XX Hz)

Three conditions simultaneously met → world-class deposit marker.
"""
import numpy as np
from typing import Optional
from .deep_channel import (
    SCHUMANN_F0, F_470KM, F_996KM,
    analyze_spectrum_for_channels, detect_substance_response,
    world_class_assessment,
)
from .alteration_indices import compute_alteration_index, compute_alteration_map


# Substance characteristic frequencies (Hz) — Yakymchuk 2020-2025 measured values
SUBSTANCE_FREQ = {
    "gold": 21.70, "silver": 21.68, "copper": 21.45, "lead_zinc": 21.62,
    "iron": 21.55, "uranium": 21.75, "ree": 21.71, "lithium": 21.74,
    "tungsten": 21.71, "tin": 21.67,
    "oil": 21.66, "gas": 21.68, "hydrogen": 21.74, "coal": 21.66,
    "fluorite": 21.73, "water": 21.60, "geothermal": 21.73,
}


def process_satellite_frt(band_cube: np.ndarray, coordinates: list[dict],
                           target_substances: list[str],
                           grid_shape: tuple = (50, 50)) -> dict:
    """Full FRT processing pipeline on satellite image data.

    Args:
        band_cube: (H, W, N_bands) array — pixel spectral profiles
        coordinates: list of {lat, lon} for each pixel
        target_substances: substances to scan for
        grid_shape: output grid dimensions

    Returns:
        dict with heatmap data, anomaly markers, and alteration maps
    """
    H, W = band_cube.shape[:2]
    n_bands = band_cube.shape[2]

    results = []
    for sid in target_substances:
        substance_freq = SUBSTANCE_FREQ.get(sid, 21.70)

        # 1. Compute surface alteration index map
        alteration_map = compute_alteration_map(band_cube, sid)

        # 2. Find top anomaly points from alteration index
        flat_alt = alteration_map.ravel()
        n_top = min(50, len(flat_alt))
        top_indices = np.argpartition(flat_alt, -n_top)[-n_top:]

        # 3. For each top anomaly point, perform FFT + deep channel detection
        for idx in top_indices:
            row, col = divmod(idx, W)
            if row >= len(coordinates) or col >= len(coordinates):
                continue

            # Extract pixel spectral profile as "signal"
            pixel_profile = band_cube[row, col, :].astype(float)

            # Detrend and prepare for FFT
            if np.std(pixel_profile) < 1e-10:
                continue
            pixel_profile = pixel_profile - np.mean(pixel_profile)

            # Pad to power of 2 for better FFT resolution
            n_fft = max(64, 2 ** int(np.ceil(np.log2(len(pixel_profile) * 4))))
            padded = np.zeros(n_fft)
            padded[:len(pixel_profile)] = pixel_profile

            # Compute power spectrum
            # Use effective sampling frequency based on band count and frequency range
            fs_eff = 50.0  # Effective sampling rate for frequency axis
            freqs = np.fft.rfftfreq(n_fft, d=1.0 / fs_eff)
            spectrum = np.abs(np.fft.rfft(padded)) ** 2
            psd_max = spectrum.max()
            if psd_max == 0:
                continue
            psd_norm = spectrum / psd_max

            # 4. Detect deep channels
            channel_result = analyze_spectrum_for_channels(freqs, psd_norm)

            # 5. Detect substance response
            substance_result = detect_substance_response(freqs, psd_norm, substance_freq)

            # 6. World-class assessment
            wc = world_class_assessment(channel_result, substance_result)

            # 7. Compute overall confidence
            alteration_val = float(alteration_map[row, col])
            confidence = 0.3 * alteration_val + 0.3 * channel_result["peak_470"] + 0.4 * substance_result["peak"]

            coord_idx = row * W + col
            if coord_idx < len(coordinates):
                results.append({
                    "lat": coordinates[coord_idx]["lat"],
                    "lon": coordinates[coord_idx]["lon"],
                    "substance_id": sid,
                    "confidence": float(np.clip(confidence, 0, 1)),
                    "alteration_index": float(alteration_val),
                    "world_class": wc["is_world_class"],
                    "world_class_score": wc["composite_score"],
                    "deep_channel": {
                        "peak_470": channel_result["peak_470"],
                        "peak_996": channel_result["peak_996"],
                        "ratio": channel_result["ratio_470_996"],
                    },
                    "substance_response": substance_result["peak"],
                    "scores": {sid: substance_result["peak"]},
                })

    return {
        "frt_results": results,
        "n_points_scanned": H * W,
        "n_anomalies": len([r for r in results if r["confidence"] > 0.5]),
        "n_world_class": len([r for r in results if r.get("world_class")]),
        "substances_scanned": target_substances,
    }


def parse_satellite_bands(file_path: str) -> dict:
    """Parse multi-band satellite GeoTIFF into FRT-compatible format.

    Supports Sentinel-2, Landsat-8, ASTER data in GeoTIFF format.
    """
    try:
        import rasterio
    except ImportError:
        return {"error": "rasterio not installed"}

    try:
        with rasterio.open(file_path) as ds:
            data = ds.read()
            n_bands, height, width = data.shape

            coordinates = []
            # Subsample to manageable grid size
            step_h = max(1, height // 100)
            step_w = max(1, width // 100)

            band_cube = []
            for row in range(0, height, step_h):
                row_data = []
                for col in range(0, width, step_w):
                    lon, lat = ds.xy(row, col)
                    coordinates.append({"lat": lat, "lon": lon})
                    row_data.append(data[:, row, col].tolist())
                band_cube.append(row_data)

            band_cube = np.array(band_cube, dtype=float)

            # Normalize each band to [0, 1]
            for b in range(band_cube.shape[2]):
                band_slice = band_cube[:, :, b]
                bmin, bmax = band_slice.min(), band_slice.max()
                if bmax > bmin:
                    band_cube[:, :, b] = (band_slice - bmin) / (bmax - bmin)

            return {
                "band_cube": band_cube,
                "coordinates": coordinates,
                "n_bands": n_bands,
                "grid_shape": band_cube.shape[:2],
                "bounds": {
                    "left": ds.bounds.left, "bottom": ds.bounds.bottom,
                    "right": ds.bounds.right, "top": ds.bounds.top,
                },
                "crs": str(ds.crs) if ds.crs else "unknown",
            }
    except Exception as e:
        return {"error": f"Failed to read satellite data: {e}"}
