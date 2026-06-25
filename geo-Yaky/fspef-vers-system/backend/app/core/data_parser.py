"""Real data parsers — convert uploaded CSV/GeoTIFF into pipeline format."""
import csv
import json
import numpy as np
from pathlib import Path
from typing import Optional


def parse_spectral_csv(file_path: str) -> dict:
    """
    Parse ground spectral survey CSV into pipeline format.

    Expected format (header row required):
      latitude,longitude,freq_0.5Hz,freq_1.0Hz,...,freq_25Hz
      55.012,73.045,0.145,0.089,...,0.003

    Or simple format:
      latitude,longitude,signal
      55.012,73.045,"0.145,0.089,0.067,..."
    """
    rows = []
    with open(file_path, 'r', encoding='utf-8-sig') as f:
        reader = csv.reader(f)
        header = next(reader, None)
        if not header:
            return {"error": "CSV file is empty"}

        header = [h.strip().lower() for h in header]

        # Detect format
        has_freq_cols = any(h.startswith('freq_') or h.startswith('f_') for h in header)
        lat_idx = _find_col(header, ['latitude', 'lat', 'y'])
        lon_idx = _find_col(header, ['longitude', 'lon', 'lng', 'x'])

        if lat_idx is None or lon_idx is None:
            return {"error": "CSV must have latitude and longitude columns"}

        # Data columns = everything that's not lat/lon
        data_indices = [i for i in range(len(header)) if i != lat_idx and i != lon_idx]

        for row in reader:
            if not row or len(row) < 3:
                continue
            try:
                lat = float(row[lat_idx])
                lon = float(row[lon_idx])
                values = [float(row[i]) for i in data_indices if i < len(row)]
                rows.append({"lat": lat, "lon": lon, "values": values})
            except (ValueError, IndexError):
                continue

    if not rows:
        return {"error": "No valid data rows found in CSV"}

    n_points = len(rows)
    n_freq = max(len(r["values"]) for r in rows)

    # Build pipeline-compatible arrays
    coordinates = [{"lat": r["lat"], "lon": r["lon"]} for r in rows]
    raw_signals = [r["values"] for r in rows]

    # Generate frequency axis from column headers or default
    if has_freq_cols:
        freq_headers = [header[i] for i in data_indices]
        frequencies = []
        for h in freq_headers:
            h_clean = h.replace('freq_', '').replace('f_', '').replace('hz', '').replace('_', '.')
            try:
                frequencies.append(float(h_clean))
            except ValueError:
                frequencies.append(0)
    else:
        frequencies = list(np.linspace(0.1, 25.0, n_freq))

    # Estimate grid shape from coordinates
    lats = sorted(set(r["lat"] for r in rows))
    lons = sorted(set(r["lon"] for r in rows))
    grid_shape = (len(lats), len(lons))

    fs = max(frequencies) * 2 if frequencies else 50.0

    return {
        "raw_signals": raw_signals,
        "fs": fs,
        "grid_shape": grid_shape,
        "coordinates": coordinates,
        "frequencies": frequencies,
        "n_points": n_points,
        "pixel_area_m2": _estimate_pixel_area(coordinates),
    }


def parse_vers_csv(file_path: str) -> dict:
    """
    Parse VERS vertical sounding CSV.

    Expected format:
      depth_m,freq_0.5Hz,freq_1.0Hz,...,freq_25Hz
      0,0.012,0.089,...
      1,0.015,0.102,...
      ...
    """
    rows = []
    with open(file_path, 'r', encoding='utf-8-sig') as f:
        reader = csv.reader(f)
        header = next(reader, None)
        if not header:
            return {"error": "VERS CSV is empty"}

        header = [h.strip().lower() for h in header]
        depth_idx = _find_col(header, ['depth_m', 'depth', 'depth_m', 'z'])
        if depth_idx is None:
            return {"error": "VERS CSV must have a depth column"}

        data_indices = [i for i in range(len(header)) if i != depth_idx]

        for row in reader:
            if not row:
                continue
            try:
                depth = float(row[depth_idx])
                values = [float(row[i]) for i in data_indices if i < len(row)]
                rows.append({"depth": depth, "values": values})
            except (ValueError, IndexError):
                continue

    if not rows:
        return {"error": "No valid rows in VERS CSV"}

    return {
        "type": "vers",
        "depths": [r["depth"] for r in rows],
        "profiles": [r["values"] for r in rows],
        "n_depths": len(rows),
        "max_depth": max(r["depth"] for r in rows),
    }


def parse_geotiff_metadata(file_path: str) -> dict:
    """
    Parse GeoTIFF and extract pixel spectral data for pipeline input.
    Treats each pixel row as a 1D signal for FFT processing.
    """
    try:
        import rasterio
    except ImportError:
        return {"error": "rasterio not installed, cannot read GeoTIFF"}

    try:
        with rasterio.open(file_path) as ds:
            data = ds.read()
            bounds = ds.bounds
            crs = str(ds.crs) if ds.crs else "unknown"
            n_bands, height, width = data.shape

            coordinates = []
            raw_signals = []

            for row in range(0, height, max(1, height // 50)):
                for col in range(0, width, max(1, width // 50)):
                    lon, lat = ds.xy(row, col)
                    pixel_values = data[:, row, col].tolist()
                    coordinates.append({"lat": lat, "lon": lon})
                    raw_signals.append(pixel_values)

            return {
                "raw_signals": raw_signals,
                "fs": 100.0,
                "grid_shape": (min(50, height), min(50, width)),
                "coordinates": coordinates,
                "n_points": len(raw_signals),
                "n_bands": n_bands,
                "bounds": {"left": bounds.left, "bottom": bounds.bottom, "right": bounds.right, "top": bounds.top},
                "crs": crs,
                "pixel_area_m2": _estimate_pixel_area(coordinates),
            }
    except Exception as e:
        return {"error": f"Failed to read GeoTIFF: {e}"}


def parse_reference_spectrum_csv(file_path: str) -> dict:
    """
    Parse a custom reference spectrum CSV.

    Expected format:
      frequency,amplitude
      0.5,0.012
      1.0,0.089
      ...
    """
    freqs, amps = [], []
    with open(file_path, 'r', encoding='utf-8-sig') as f:
        reader = csv.reader(f)
        next(reader, None)  # skip header
        for row in reader:
            if len(row) >= 2:
                try:
                    freqs.append(float(row[0]))
                    amps.append(float(row[1]))
                except ValueError:
                    continue

    if not freqs:
        return {"error": "No valid data in reference spectrum CSV"}

    return {
        "frequencies": freqs,
        "amplitudes": amps,
        "n_points": len(freqs),
        "freq_min": min(freqs),
        "freq_max": max(freqs),
    }


def _find_col(header: list, candidates: list) -> Optional[int]:
    for c in candidates:
        for i, h in enumerate(header):
            if h == c or h.startswith(c):
                return i
    return None


def _estimate_pixel_area(coordinates: list) -> float:
    if len(coordinates) < 2:
        return 100.0
    lats = [c["lat"] for c in coordinates]
    lons = [c["lon"] for c in coordinates]
    unique_lats = sorted(set(round(l, 6) for l in lats))
    unique_lons = sorted(set(round(l, 6) for l in lons))
    if len(unique_lats) < 2 or len(unique_lons) < 2:
        return 100.0
    dlat = abs(unique_lats[1] - unique_lats[0])
    dlon = abs(unique_lons[1] - unique_lons[0])
    return dlat * 111000 * dlon * 111000 * max(1, (len(lats) / max(len(unique_lats), 1)))
