"""Stage 1: Preprocessing — denoising, baseline correction, normalization."""
import numpy as np
from ..core.signal_processing import denoise_signal


def run(data: dict, params: dict) -> dict:
    """
    Input:  data["raw_signals"] — list of 1D numpy arrays (per-location measurements)
            data["fs"] — sampling frequency
    Output: data["clean_signals"] — list of denoised arrays
    """
    raw = data.get("raw_signals", [])
    fs = data.get("fs", 100.0)
    method = params.get("denoise_method", "wavelet")

    clean = []
    for sig in raw:
        sig = np.array(sig, dtype=float)
        # Baseline correction
        baseline = np.polyval(np.polyfit(np.arange(len(sig)), sig, 3), np.arange(len(sig)))
        sig = sig - baseline
        # Normalize
        max_val = np.max(np.abs(sig))
        if max_val > 0:
            sig = sig / max_val
        # Denoise
        sig = denoise_signal(sig, method=method)
        clean.append(sig)

    data["clean_signals"] = clean
    data["fs"] = fs
    return data
