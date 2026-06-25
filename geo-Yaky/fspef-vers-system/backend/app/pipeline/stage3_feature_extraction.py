"""Stage 3: Feature extraction — peak frequencies, band energy, Q factor."""
import numpy as np
from ..core.signal_processing import find_peaks


def run(data: dict, params: dict) -> dict:
    """
    Input:  data["fft_results"] — list of {frequencies, magnitudes}
    Output: data["features"] — list of {peaks, band_energies, feature_vector}
    """
    fft_results = data.get("fft_results", [])
    n_bands = params.get("n_bands", 10)

    features = []
    for fft_data in fft_results:
        freqs = fft_data["frequencies"]
        mags = fft_data["magnitudes"]

        # Peak detection
        peaks = find_peaks(mags, freqs, min_height=0.1, min_distance=5)

        # Band energy distribution
        band_edges = np.linspace(0, freqs[-1] if len(freqs) > 0 else 1, n_bands + 1)
        band_energies = []
        for i in range(n_bands):
            mask = (freqs >= band_edges[i]) & (freqs < band_edges[i + 1])
            band_energies.append(float(np.sum(mags[mask] ** 2)))

        # Feature vector: [peak_freqs..., band_energies...]
        top_peaks = peaks[:5] if len(peaks) >= 5 else peaks
        feature_vector = [p["frequency"] for p in top_peaks]
        feature_vector += [p["q_factor"] for p in top_peaks]
        feature_vector += band_energies

        features.append({
            "peaks": peaks[:10],
            "band_energies": band_energies,
            "feature_vector": feature_vector,
        })

    data["features"] = features
    return data
