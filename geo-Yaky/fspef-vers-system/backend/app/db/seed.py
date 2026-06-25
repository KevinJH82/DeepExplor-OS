import json
import numpy as np
from sqlalchemy.orm import Session
from ..models.substance import Substance, SpectralReference

# Schumann resonance base frequency
SCHUMANN_F0 = 7.83

# Deep channel characteristic frequencies
F_470KM = SCHUMANN_F0 * (470.0 / 996.0)  # ≈ 3.70 Hz
F_996KM = SCHUMANN_F0                     # = 7.83 Hz

SUBSTANCE_CONFIGS = [
    # --- Metals ---
    {
        "id": "gold", "name": "金矿", "description": "Gold — 世界级金矿床特征共振 (Muruntau, Boddington)",
        "freq_min": 19.0, "freq_max": 24.0, "c_equivalent": 350, "threshold": 0.68,
        "color": "#FFD700", "icon": "gem",
        "peaks": [(21.70, 1.0, 16), (21.50, 0.7, 12), (21.90, 0.4, 8)],
        "channel_threshold_470": 0.78, "channel_ratio_threshold": 3.2, "substance_resp_threshold": 0.68,
    },
    {
        "id": "silver", "name": "银矿", "description": "Silver — 银矿化带特征共振 (Al-OH + Fe-OH)",
        "freq_min": 19.0, "freq_max": 24.0, "c_equivalent": 330, "threshold": 0.68,
        "color": "#C0C0C0", "icon": "gem",
        "peaks": [(21.68, 1.0, 15), (21.48, 0.65, 11), (21.88, 0.35, 7)],
        "channel_threshold_470": 0.78, "channel_ratio_threshold": 3.2, "substance_resp_threshold": 0.68,
    },
    {
        "id": "copper", "name": "铜矿", "description": "Copper — 斑岩铜矿特征共振 (Escondida, 紫金山)",
        "freq_min": 19.0, "freq_max": 24.0, "c_equivalent": 320, "threshold": 0.70,
        "color": "#B87333", "icon": "mountain",
        "peaks": [(21.45, 1.0, 18), (21.25, 0.6, 13), (21.65, 0.35, 8)],
        "channel_threshold_470": 0.79, "channel_ratio_threshold": 3.3, "substance_resp_threshold": 0.70,
    },
    {
        "id": "lead_zinc", "name": "铅锌矿", "description": "Lead-Zinc — 铅锌多金属矿特征共振",
        "freq_min": 19.0, "freq_max": 24.0, "c_equivalent": 310, "threshold": 0.68,
        "color": "#7B8B6F", "icon": "mountain",
        "peaks": [(21.62, 1.0, 14), (21.42, 0.65, 10), (21.82, 0.35, 7)],
        "channel_threshold_470": 0.78, "channel_ratio_threshold": 3.2, "substance_resp_threshold": 0.68,
    },
    {
        "id": "iron", "name": "铁矿", "description": "Iron — 铁矿床特征共振",
        "freq_min": 19.0, "freq_max": 24.0, "c_equivalent": 340, "threshold": 0.70,
        "color": "#A0522D", "icon": "mountain",
        "peaks": [(21.55, 1.0, 15), (21.35, 0.6, 11), (21.75, 0.3, 7)],
        "channel_threshold_470": 0.80, "channel_ratio_threshold": 3.3, "substance_resp_threshold": 0.70,
    },
    {
        "id": "uranium", "name": "铀矿", "description": "Uranium — 砂岩型/侵入型铀矿特征共振 (Olympic Dam)",
        "freq_min": 19.0, "freq_max": 24.0, "c_equivalent": 300, "threshold": 0.68,
        "color": "#00FF7F", "icon": "atom",
        "peaks": [(21.75, 1.0, 17), (21.55, 0.65, 12), (21.95, 0.35, 8)],
        "channel_threshold_470": 0.78, "channel_ratio_threshold": 3.2, "substance_resp_threshold": 0.68,
    },
    # --- Critical Minerals ---
    {
        "id": "ree", "name": "稀土矿", "description": "Rare Earth Elements — 碳酸岩/碱性花岗岩稀土特征共振 (白云鄂博)",
        "freq_min": 19.0, "freq_max": 24.0, "c_equivalent": 290, "threshold": 0.70,
        "color": "#9B59B6", "icon": "gem",
        "peaks": [(21.71, 1.0, 16), (21.51, 0.6, 11), (21.91, 0.35, 7)],
        "channel_threshold_470": 0.79, "channel_ratio_threshold": 3.3, "substance_resp_threshold": 0.70,
    },
    {
        "id": "lithium", "name": "锂矿", "description": "Lithium — 锂辉石伟晶岩特征共振 (甲基卡, Greenbushes)",
        "freq_min": 19.0, "freq_max": 24.0, "c_equivalent": 280, "threshold": 0.70,
        "color": "#E74C3C", "icon": "battery",
        "peaks": [(21.74, 1.0, 17), (21.54, 0.6, 12), (21.94, 0.3, 7)],
        "channel_threshold_470": 0.79, "channel_ratio_threshold": 3.3, "substance_resp_threshold": 0.70,
    },
    {
        "id": "tungsten", "name": "钨矿", "description": "Tungsten — 白钨矿/黑钨矿云英岩化特征共振 (柿竹园)",
        "freq_min": 19.0, "freq_max": 24.0, "c_equivalent": 360, "threshold": 0.71,
        "color": "#708090", "icon": "mountain",
        "peaks": [(21.71, 1.0, 16), (21.51, 0.65, 11), (21.91, 0.3, 7)],
        "channel_threshold_470": 0.81, "channel_ratio_threshold": 3.4, "substance_resp_threshold": 0.71,
    },
    {
        "id": "tin", "name": "锡矿", "description": "Tin — 锡石-硫化物矿床特征共振",
        "freq_min": 19.0, "freq_max": 24.0, "c_equivalent": 340, "threshold": 0.70,
        "color": "#D4AF37", "icon": "mountain",
        "peaks": [(21.67, 1.0, 15), (21.47, 0.6, 11), (21.87, 0.3, 7)],
        "channel_threshold_470": 0.80, "channel_ratio_threshold": 3.3, "substance_resp_threshold": 0.70,
    },
    # --- Energy ---
    {
        "id": "oil", "name": "石油/原油", "description": "Petroleum — 烃微渗异常特征共振",
        "freq_min": 19.0, "freq_max": 24.0, "c_equivalent": 300, "threshold": 0.70,
        "color": "#8B4513", "icon": "droplet",
        "peaks": [(21.66, 1.0, 16), (21.46, 0.7, 12), (21.86, 0.4, 8)],
        "channel_threshold_470": 0.80, "channel_ratio_threshold": 3.3, "substance_resp_threshold": 0.70,
    },
    {
        "id": "gas", "name": "天然气", "description": "Natural Gas — 甲烷特征共振",
        "freq_min": 19.0, "freq_max": 24.0, "c_equivalent": 250, "threshold": 0.70,
        "color": "#FF6600", "icon": "wind",
        "peaks": [(21.68, 1.0, 18), (21.48, 0.6, 13), (21.88, 0.35, 8)],
        "channel_threshold_470": 0.80, "channel_ratio_threshold": 3.3, "substance_resp_threshold": 0.70,
    },
    {
        "id": "hydrogen", "name": "氢气", "description": "Hydrogen — 深部无机成因氢特征共振",
        "freq_min": 19.0, "freq_max": 24.0, "c_equivalent": 200, "threshold": 0.68,
        "color": "#00AAFF", "icon": "atom",
        "peaks": [(21.74, 1.0, 20), (21.54, 0.55, 14), (21.94, 0.3, 8)],
        "channel_threshold_470": 0.78, "channel_ratio_threshold": 3.2, "substance_resp_threshold": 0.68,
    },
    {
        "id": "coal", "name": "煤矿", "description": "Coal — 煤田特征共振",
        "freq_min": 19.0, "freq_max": 24.0, "c_equivalent": 260, "threshold": 0.70,
        "color": "#2C3E50", "icon": "mountain",
        "peaks": [(21.66, 1.0, 14), (21.46, 0.6, 10), (21.86, 0.3, 7)],
        "channel_threshold_470": 0.80, "channel_ratio_threshold": 3.3, "substance_resp_threshold": 0.70,
    },
    # --- Industrial Minerals ---
    {
        "id": "fluorite", "name": "萤石", "description": "Fluorite (CaF₂) — 萤石矿特征共振",
        "freq_min": 19.0, "freq_max": 24.0, "c_equivalent": 270, "threshold": 0.71,
        "color": "#00CED1", "icon": "gem",
        "peaks": [(21.73, 1.0, 15), (21.53, 0.65, 11), (21.93, 0.3, 7)],
        "channel_threshold_470": 0.81, "channel_ratio_threshold": 3.4, "substance_resp_threshold": 0.71,
    },
    # --- Water & Geothermal ---
    {
        "id": "water", "name": "地下水/深层水", "description": "Groundwater — 流体特征共振",
        "freq_min": 19.0, "freq_max": 24.0, "c_equivalent": 280, "threshold": 0.68,
        "color": "#0066FF", "icon": "waves",
        "peaks": [(21.60, 1.0, 22), (21.40, 0.75, 16), (21.80, 0.4, 10)],
        "channel_threshold_470": 0.78, "channel_ratio_threshold": 3.2, "substance_resp_threshold": 0.68,
    },
    {
        "id": "geothermal", "name": "地热", "description": "Geothermal — 高温地热田特征共振 (冰岛, 羊八井)",
        "freq_min": 19.0, "freq_max": 24.0, "c_equivalent": 250, "threshold": 0.75,
        "color": "#FF4500", "icon": "flame",
        "peaks": [(21.73, 1.0, 18), (21.53, 0.7, 13), (21.93, 0.4, 8)],
        "channel_threshold_470": 0.85, "channel_ratio_threshold": 4.0, "substance_resp_threshold": 0.75,
    },
]


def generate_reference_spectrum(peaks, n_points=2048, freq_min=19.0, freq_max=24.0):
    """Generate synthetic Lorentzian reference spectrum for a substance."""
    freqs = np.linspace(freq_min, freq_max, n_points)
    spectrum = np.zeros(n_points)
    for f0, amp, Q in peaks:
        bandwidth = f0 / Q
        spectrum += amp / (1 + ((freqs - f0) / (bandwidth / 2)) ** 2)
    noise = np.random.exponential(0.005, n_points) * (1.0 / (freqs + 0.1))
    spectrum += noise
    return freqs.tolist(), spectrum.tolist()


def seed_database(db: Session):
    """Populate database with substance configs and reference spectra."""
    # Check if already seeded
    if db.query(Substance).first():
        return

    for cfg in SUBSTANCE_CONFIGS:
        peaks = cfg["peaks"]
        substance = Substance(**{k: v for k, v in cfg.items() if k != "peaks" and k not in (
            "channel_threshold_470", "channel_ratio_threshold", "substance_resp_threshold"
        )})
        db.add(substance)
        db.flush()

        # Generate 3 reference spectra per substance with slight variations
        for i in range(3):
            np.random.seed((hash(cfg["id"]) + i) % (2**31))
            varied_peaks = [(f * (1 + 0.02 * np.random.randn()), a * (1 + 0.05 * np.random.randn()), Q) for f, a, Q in peaks]
            freqs, amps = generate_reference_spectrum(varied_peaks, freq_min=cfg["freq_min"], freq_max=cfg["freq_max"])
            ref = SpectralReference(
                substance_id=cfg["id"],
                name=f"{cfg['name']} 参考光谱 #{i+1}",
                description=f"合成参考光谱 — {cfg['name']}类型 {i+1}",
                source="synthetic",
                freq_data=json.dumps(freqs),
                amp_data=json.dumps(amps),
                n_points=len(freqs),
                freq_min=min(freqs),
                freq_max=max(freqs),
            )
            db.add(ref)

    db.commit()
