"""Stage 2: Frequency domain transform — FFT (CWT optional, skipped for performance)."""
import numpy as np
from ..core.signal_processing import compute_fft


def run(data: dict, params: dict) -> dict:
    clean = data.get("clean_signals", [])
    fs = data.get("fs", 100.0)

    fft_results = []
    for sig in clean:
        freqs, mags = compute_fft(sig, fs=fs)
        fft_results.append({"frequencies": freqs, "magnitudes": mags})

    data["fft_results"] = fft_results
    data["cwt_results"] = []  # Skipped for performance; FFT is sufficient for spectral matching
    return data
