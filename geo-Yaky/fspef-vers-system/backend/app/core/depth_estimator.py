"""Depth estimation using H = C/(2f) formula + deep channel depth-frequency mapping."""
import numpy as np

# Schumann resonance constants
SCHUMANN_F0 = 7.83  # Hz — Earth's fundamental electromagnetic resonance

# Deep channel parameters (Yakymchuk 2020)
F_470KM = SCHUMANN_F0 * (470.0 / 996.0)  # ≈ 3.702 Hz
F_996KM = SCHUMANN_F0                     # = 7.83 Hz

# Channel attenuation coefficients
ATTENUATION_470 = 0.03  # Np/km — young channel (low loss)
ATTENUATION_996 = 0.18  # Np/km — old channel (high loss)


def calculate_depth(f_resonance: float, c_equivalent: float) -> float:
    """H = C / (2f) — half-wavelength standing wave resonance depth."""
    if f_resonance <= 0:
        return float("inf")
    return c_equivalent / (2.0 * f_resonance)


def depth_from_channel_frequency(f: float) -> float:
    """Map spectral peak frequency to depth via Schumann harmonics.

    f_depth = 7.83 × (d / 996)
    → d = 996 × (f / 7.83)

    Used for deep structure mapping (470km, 996km channels).
    """
    if f <= 0:
        return 0.0
    return 996.0 * (f / SCHUMANN_F0)


def channel_signal_strength(depth_km: float, is_young: bool = True) -> float:
    """Estimate signal attenuation through deep channel.

    Based on Yakymchuk 2020 measured attenuation:
    - Young (470km): α ≈ 0.03 Np/km
    - Old (996km): α ≈ 0.18 Np/km
    """
    alpha = ATTENUATION_470 if is_young else ATTENUATION_996
    return float(np.exp(-2 * alpha * depth_km))


def aggregate_depths(peaks: list[dict], c_equivalent: float, weights: str = "energy") -> dict:
    """Aggregate depth from multiple resonance peaks."""
    if not peaks:
        return {"depth": 0.0, "uncertainty": 0.0, "n_peaks": 0}

    depths = []
    w = []
    for peak in peaks:
        d = calculate_depth(peak["frequency"], c_equivalent)
        depths.append(d)
        if weights == "energy":
            w.append(peak["amplitude"])
        elif weights == "q":
            w.append(peak.get("q_factor", 1.0))
        else:
            w.append(1.0)

    depths = np.array(depths)
    w = np.array(w)
    w_sum = w.sum()
    if w_sum == 0:
        w = np.ones_like(w)

    mean_depth = float(np.average(depths, weights=w))
    std_depth = float(np.sqrt(np.average((depths - mean_depth) ** 2, weights=w)))

    return {
        "depth": mean_depth,
        "uncertainty": std_depth,
        "n_peaks": len(peaks),
        "individual_depths": depths.tolist(),
        "frequencies": [p["frequency"] for p in peaks],
    }
