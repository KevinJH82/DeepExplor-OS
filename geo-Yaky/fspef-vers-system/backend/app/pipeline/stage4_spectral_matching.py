"""Stage 4: Spectral matching against reference library."""
import numpy as np
from ..core.spectral_library import SpectralLibrary


def run(data: dict, params: dict) -> dict:
    """
    Input:  data["fft_results"] — list of {frequencies, magnitudes}
            data["spectral_library"] — SpectralLibrary instance
    Output: data["match_results"] — list of {matches: [{substance_id, score}], best_match}
    """
    lib: SpectralLibrary = data["spectral_library"]
    fft_results = data.get("fft_results", [])
    method = params.get("match_method", "cosine")

    match_results = []
    for fft_data in fft_results:
        obs_freqs = fft_data["frequencies"]
        obs_mags = fft_data["magnitudes"]
        matches = lib.match(obs_freqs, obs_mags, method=method)
        best = matches[0] if matches else {"substance_id": "unknown", "score": 0.0}
        match_results.append({
            "matches": matches,
            "best_match": best,
        })

    data["match_results"] = match_results
    return data
