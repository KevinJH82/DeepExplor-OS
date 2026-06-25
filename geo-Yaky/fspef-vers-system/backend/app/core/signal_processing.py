"""Core DSP primitives: FFT, CWT, filtering, peak detection."""
import numpy as np
from scipy import signal, fft
import pywt


def compute_fft(data: np.ndarray, fs: float = 1.0, window: str = "hann") -> tuple[np.ndarray, np.ndarray]:
    """Windowed FFT returning (frequencies, magnitudes)."""
    n = len(data)
    w = signal.get_window(window, n)
    spectrum = fft.rfft(data * w)
    freqs = fft.rfftfreq(n, d=1.0 / fs)
    magnitudes = np.abs(spectrum)
    return freqs, magnitudes


def compute_psd(data: np.ndarray, fs: float = 1.0, nperseg: int = 256) -> tuple[np.ndarray, np.ndarray]:
    """Power spectral density via Welch's method."""
    freqs, psd = signal.welch(data, fs=fs, nperseg=min(nperseg, len(data)))
    return freqs, psd


def compute_cwt(data: np.ndarray, fs: float = 1.0, wavelet: str = "morl",
                freq_range: tuple[float, float] | None = None) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Continuous wavelet transform returning (frequencies, coefficients, times)."""
    n = len(data)
    if freq_range:
        max_scale = fs / freq_range[0] if freq_range[0] > 0 else 128
        min_scale = fs / freq_range[1] if freq_range[1] > 0 else 1
    else:
        max_scale = 128
        min_scale = 1
    scales = np.arange(max(1, int(min_scale)), min(int(max_scale) + 1, n // 2))
    if len(scales) == 0:
        scales = np.arange(1, 33)
    coefficients, freqs = pywt.cwt(data, scales, wavelet, sampling_period=1.0 / fs)
    times = np.arange(n) / fs
    return freqs, np.abs(coefficients), times


def bandpass_filter(data: np.ndarray, fs: float, lowcut: float, highcut: float, order: int = 4) -> np.ndarray:
    """Butterworth bandpass filter."""
    nyq = 0.5 * fs
    low = lowcut / nyq
    high = highcut / nyq
    low = max(low, 0.001)
    high = min(high, 0.999)
    b, a = signal.butter(order, [low, high], btype="band")
    return signal.filtfilt(b, a, data)


def denoise_signal(data: np.ndarray, method: str = "wavelet", level: int = 3) -> np.ndarray:
    """Denoise using wavelet thresholding or Savitzky-Golay."""
    if method == "wavelet":
        w = pywt.Wavelet("db4")
        coeffs = pywt.wavedec(data, w, level=level)
        sigma = np.median(np.abs(coeffs[-1])) / 0.6745
        threshold_val = sigma * np.sqrt(2 * np.log(len(data)))
        coeffs[1:] = [pywt.threshold(c, threshold_val, mode="soft") for c in coeffs[1:]]
        return pywt.waverec(coeffs, w)[: len(data)]
    elif method == "savgol":
        window = min(31, len(data) if len(data) % 2 == 1 else len(data) - 1)
        if window < 5:
            return data
        return signal.savgol_filter(data, window, 3)
    else:
        return data


def find_peaks(data: np.ndarray, freqs: np.ndarray, min_height: float = 0.1,
               min_distance: int = 5) -> list[dict]:
    """Find resonance peaks with frequency, amplitude, Q factor."""
    peaks_idx, properties = signal.find_peaks(data, height=min_height * np.max(data),
                                               distance=min_distance)
    results = []
    for idx in peaks_idx:
        q_factor = _estimate_q_factor(data, idx)
        results.append({
            "frequency": float(freqs[idx]),
            "amplitude": float(data[idx]),
            "index": int(idx),
            "q_factor": float(q_factor),
        })
    results.sort(key=lambda x: x["amplitude"], reverse=True)
    return results


def _estimate_q_factor(spectrum: np.ndarray, peak_idx: int) -> float:
    """Estimate Q factor from -3dB bandwidth around a peak."""
    peak_val = spectrum[peak_idx]
    half_power = peak_val / np.sqrt(2)
    left = peak_idx
    while left > 0 and spectrum[left] > half_power:
        left -= 1
    right = peak_idx
    while right < len(spectrum) - 1 and spectrum[right] > half_power:
        right += 1
    bandwidth = max(right - left, 1)
    return max(peak_idx / bandwidth, 1.0)


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """Cosine similarity between two vectors."""
    norm_a = np.linalg.norm(a)
    norm_b = np.linalg.norm(b)
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return float(np.dot(a, b) / (norm_a * norm_b))


def pearson_correlation(a: np.ndarray, b: np.ndarray) -> float:
    """Pearson correlation coefficient."""
    if len(a) != len(b):
        min_len = min(len(a), len(b))
        a, b = a[:min_len], b[:min_len]
    if np.std(a) == 0 or np.std(b) == 0:
        return 0.0
    return float(np.corrcoef(a, b)[0, 1])
