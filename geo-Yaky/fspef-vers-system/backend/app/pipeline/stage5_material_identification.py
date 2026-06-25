"""Stage 5: Material identification with confidence scoring + deep channel assessment."""
import numpy as np
from ..core.deep_channel import (
    detect_deep_channels, analyze_spectrum_for_channels,
    detect_substance_response, world_class_assessment,
)
from ..db.seed import SUBSTANCE_CONFIGS


def run(data: dict, params: dict) -> dict:
    """
    Input:  data["match_results"] — list of {matches, best_match}
            data["features"] — list of {peaks, ...}
            data["fft_results"] — list of {frequencies, magnitudes} (for deep channel detection)
    Output: data["identifications"] — list of {substance_id, confidence, scores, world_class_assessment}
    """
    match_results = data.get("match_results", [])
    features = data.get("features", [])
    fft_results = data.get("fft_results", [])

    # Build substance config lookup
    configs = {c["id"]: c for c in SUBSTANCE_CONFIGS}

    identifications = []
    for i, mr in enumerate(match_results):
        best = mr.get("best_match", {})
        substance_id = best.get("substance_id", "unknown")
        match_score = best.get("score", 0.0)

        feat = features[i] if i < len(features) else {"peaks": [], "band_energies": []}
        n_peaks = len(feat.get("peaks", []))
        avg_q = np.mean([p["q_factor"] for p in feat["peaks"]]) if feat["peaks"] else 1.0
        snr = float(np.max(feat.get("band_energies", [1]))) / (float(np.mean(feat.get("band_energies", [1]))) + 1e-10)

        snr_norm = min(snr / 10.0, 1.0)
        q_norm = min(avg_q / 20.0, 1.0)
        confidence = 0.6 * match_score + 0.2 * snr_norm + 0.2 * q_norm

        scores = {}
        for m in mr.get("matches", []):
            scores[m["substance_id"]] = m["score"]

        # Deep channel detection via FFT power spectrum
        wc_assessment = None
        if i < len(fft_results):
            fft_data = fft_results[i]
            freqs = np.array(fft_data.get("frequencies", []))
            mags = np.array(fft_data.get("magnitudes", []))

            if len(freqs) > 4 and len(mags) > 4:
                psd = mags ** 2
                psd_max = psd.max()
                if psd_max > 0:
                    psd_norm = psd / psd_max

                    channel_result = analyze_spectrum_for_channels(freqs, psd_norm)

                    cfg = configs.get(substance_id, {})
                    substance_freq = cfg.get("peaks", [(21.70, 1.0, 16)])[0][0]
                    channel_threshold = cfg.get("channel_threshold_470", 0.78)
                    ratio_threshold = cfg.get("channel_ratio_threshold", 3.2)
                    substance_threshold = cfg.get("substance_resp_threshold", 0.68)

                    substance_result = detect_substance_response(freqs, psd_norm, substance_freq)

                    wc_assessment = world_class_assessment(
                        channel_result, substance_result,
                        channel_threshold=channel_threshold,
                        ratio_threshold=ratio_threshold,
                        substance_threshold=substance_threshold,
                    )

                    # Boost confidence if world-class criteria are met
                    if wc_assessment["is_world_class"]:
                        confidence = min(1.0, confidence * 1.2)

        ident_entry = {
            "substance_id": substance_id,
            "confidence": float(confidence),
            "match_score": float(match_score),
            "n_peaks": n_peaks,
            "scores": scores,
        }
        if wc_assessment:
            ident_entry["world_class_assessment"] = wc_assessment

        identifications.append(ident_entry)

    data["identifications"] = identifications
    return data
