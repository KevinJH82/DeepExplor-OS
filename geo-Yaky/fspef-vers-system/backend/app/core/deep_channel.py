"""Deep channel detection — 470km young granite channel + 996km old channel.

Based on Yakymchuk 2020-2025 methodology:
- Schumann resonance base: 7.83 Hz
- 470km young channel frequency: 7.83 × (470/996) ≈ 3.70 Hz
- 996km old channel frequency: 7.83 Hz
- World-class deposit criterion: peak_470 > 0.78 AND peak_470 > peak_996 × 3.2
"""
import numpy as np
from .signal_processing import compute_fft

SCHUMANN_F0 = 7.83
F_470KM = SCHUMANN_F0 * (470.0 / 996.0)  # ≈ 3.702 Hz
F_996KM = SCHUMANN_F0                     # = 7.83 Hz

# Default detection bandwidth (±0.12 × f0 as per Yakymchuk 2020)
BANDWIDTH_RATIO = 0.12


def detect_deep_channels(signal: np.ndarray, fs: float) -> dict:
    """Detect 470km and 996km channel peaks from a signal's power spectrum.

    Args:
        signal: 1D time-domain signal (raw or preprocessed)
        fs: sampling frequency in Hz

    Returns:
        dict with peak_470, peak_996, ratio, channel_present, spectrum info
    """
    if len(signal) < 8:
        return _empty_result("signal too short")

    # Compute power spectrum
    n = len(signal)
    freqs = np.fft.rfftfreq(n, d=1.0 / fs)
    spectrum = np.abs(np.fft.rfft(signal)) ** 2
    if spectrum.max() == 0:
        return _empty_result("zero spectrum")
    psd = spectrum / spectrum.max()

    return analyze_spectrum_for_channels(freqs, psd)


def analyze_spectrum_for_channels(freqs: np.ndarray, psd: np.ndarray) -> dict:
    """Analyze an existing power spectrum for deep channel signatures.

    Args:
        freqs: frequency array in Hz
        psd: normalized power spectral density (0 to 1)

    Returns:
        dict with channel detection results
    """
    # Search for 470km peak
    bw_470 = BANDWIDTH_RATIO * SCHUMANN_F0
    mask_470 = np.abs(freqs - F_470KM) < bw_470
    peak_470 = float(np.max(psd[mask_470])) if np.any(mask_470) else 0.0

    # Search for 996km peak
    bw_996 = BANDWIDTH_RATIO * SCHUMANN_F0
    mask_996 = np.abs(freqs - F_996KM) < bw_996
    peak_996 = float(np.max(psd[mask_996])) if np.any(mask_996) else 0.0

    ratio = peak_470 / peak_996 if peak_996 > 0 else float('inf')

    return {
        "peak_470": peak_470,
        "peak_996": peak_996,
        "ratio_470_996": ratio,
        "f_470km": F_470KM,
        "f_996km": F_996KM,
        "channel_present": peak_470 > 0.0,
        "frequencies": freqs.tolist(),
        "psd": psd.tolist(),
    }


def detect_substance_response(freqs: np.ndarray, psd: np.ndarray,
                               substance_freq: float, bandwidth: float = 0.12) -> dict:
    """Detect target substance characteristic frequency response in spectrum.

    Args:
        freqs: frequency array in Hz
        psd: normalized PSD
        substance_freq: target substance characteristic frequency (e.g., 21.70 for gold)
        bandwidth: search bandwidth as fraction of substance_freq

    Returns:
        dict with peak value and frequency
    """
    bw = bandwidth * substance_freq
    mask = np.abs(freqs - substance_freq) < bw
    if not np.any(mask):
        return {"peak": 0.0, "freq": substance_freq, "detected": False}

    idx = np.argmax(psd[mask])
    matched_freqs = freqs[mask]
    peak_val = float(psd[mask][idx])
    peak_freq = float(matched_freqs[idx])

    return {
        "peak": peak_val,
        "freq": peak_freq,
        "target_freq": substance_freq,
        "detected": peak_val > 0.1,
    }


def world_class_assessment(channel_result: dict, substance_result: dict,
                            channel_threshold: float = 0.78,
                            ratio_threshold: float = 3.2,
                            substance_threshold: float = 0.68) -> dict:
    """Three-tier world-class deposit assessment (Yakymchuk 2020 criterion).

    All three conditions must be met:
    1. 470km young channel peak > threshold
    2. 470km peak > 996km peak × ratio_threshold
    3. Target substance response > substance_threshold
    """
    peak_470 = channel_result.get("peak_470", 0.0)
    ratio = channel_result.get("ratio_470_996", 0.0)
    substance_peak = substance_result.get("peak", 0.0)

    cond_channel = peak_470 >= channel_threshold
    cond_ratio = ratio >= ratio_threshold
    cond_substance = substance_peak >= substance_threshold
    is_world_class = cond_channel and cond_ratio and cond_substance

    # Composite score (0-1)
    score = min(1.0, (peak_470 / channel_threshold + ratio / ratio_threshold +
                       substance_peak / substance_threshold) / 3.0)

    return {
        "is_world_class": is_world_class,
        "composite_score": score,
        "conditions": {
            "channel_470": {"value": peak_470, "threshold": channel_threshold, "met": cond_channel},
            "ratio_470_996": {"value": ratio, "threshold": ratio_threshold, "met": cond_ratio},
            "substance_response": {"value": substance_peak, "threshold": substance_threshold, "met": cond_substance},
        },
    }


def _empty_result(reason: str) -> dict:
    return {
        "peak_470": 0.0, "peak_996": 0.0, "ratio_470_996": 0.0,
        "f_470km": F_470KM, "f_996km": F_996KM,
        "channel_present": False, "error": reason,
        "frequencies": [], "psd": [],
    }
