"""Spectral library matching engine."""
import json
import numpy as np
from .signal_processing import cosine_similarity, pearson_correlation


class SpectralLibrary:
    """Manages reference spectra and performs matching."""

    def __init__(self):
        self._references: dict[str, list[dict]] = {}

    def load_from_db(self, db_refs: list):
        """Load reference spectra from database records."""
        self._references.clear()
        for ref in db_refs:
            sid = ref.substance_id
            if sid not in self._references:
                self._references[sid] = []
            self._references[sid].append({
                "id": ref.id,
                "name": ref.name,
                "frequencies": np.array(json.loads(ref.freq_data)),
                "amplitudes": np.array(json.loads(ref.amp_data)),
            })

    def get_substances(self) -> list[str]:
        return list(self._references.keys())

    def match(self, observed_freqs: np.ndarray, observed_amps: np.ndarray,
              method: str = "cosine") -> list[dict]:
        """Match observed spectrum against all references."""
        results = []
        for substance_id, refs in self._references.items():
            best_score = 0.0
            best_ref = None
            for ref in refs:
                score = self._compute_match(observed_freqs, observed_amps,
                                            ref["frequencies"], ref["amplitudes"], method)
                if score > best_score:
                    best_score = score
                    best_ref = ref
            results.append({
                "substance_id": substance_id,
                "score": best_score,
                "reference_name": best_ref["name"] if best_ref else "",
            })
        results.sort(key=lambda x: x["score"], reverse=True)
        return results

    def match_substance(self, observed_freqs: np.ndarray, observed_amps: np.ndarray,
                        substance_id: str, method: str = "cosine") -> dict:
        """Match against a specific substance's references."""
        refs = self._references.get(substance_id, [])
        best_score = 0.0
        best_ref = None
        for ref in refs:
            score = self._compute_match(observed_freqs, observed_amps,
                                        ref["frequencies"], ref["amplitudes"], method)
            if score > best_score:
                best_score = score
                best_ref = ref
        return {
            "substance_id": substance_id,
            "score": best_score,
            "reference_name": best_ref["name"] if best_ref else "",
        }

    @staticmethod
    def _compute_match(obs_freq: np.ndarray, obs_amp: np.ndarray,
                       ref_freq: np.ndarray, ref_amp: np.ndarray, method: str) -> float:
        """Interpolate to common frequency grid and compute similarity."""
        common_freq = np.union1d(obs_freq, ref_freq)
        obs_interp = np.interp(common_freq, obs_freq, obs_amp)
        ref_interp = np.interp(common_freq, ref_freq, ref_amp)
        obs_norm = obs_interp / (np.max(np.abs(obs_interp)) + 1e-10)
        ref_norm = ref_interp / (np.max(np.abs(ref_interp)) + 1e-10)
        if method == "pearson":
            return pearson_correlation(obs_norm, ref_norm)
        return cosine_similarity(obs_norm, ref_norm)
