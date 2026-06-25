from pydantic import BaseModel
from datetime import datetime


# --- Substance ---
class SubstanceOut(BaseModel):
    id: str
    name: str
    description: str | None
    freq_min: float
    freq_max: float
    c_equivalent: float
    threshold: float
    color: str


# --- Spectral Reference ---
class SpectralRefOut(BaseModel):
    id: str
    substance_id: str
    name: str
    description: str | None
    source: str | None
    freq_data: list[float]
    amp_data: list[float]
    n_points: int
    freq_min: float
    freq_max: float


class SpectralRefCreate(BaseModel):
    substance_id: str
    name: str
    description: str | None = None
    source: str | None = None
    freq_data: list[float]
    amp_data: list[float]


# --- Upload ---
class UploadOut(BaseModel):
    id: str
    filename: str
    file_type: str
    file_size: int | None
    survey_type: str | None
    created_at: datetime


# --- Job ---
class JobCreate(BaseModel):
    upload_id: str | None = None
    target_substances: list[str] = ["oil", "gas"]
    parameters: dict | None = None
    demo_mode: bool = False


class JobOut(BaseModel):
    id: str
    upload_id: str | None
    status: str
    current_stage: int
    percent: float
    target_substances: str
    error_message: str | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None


class JobProgress(BaseModel):
    job_id: str
    stage: int
    stage_name: str
    percent: float
    message: str


# --- Results ---
class AnomalyOut(BaseModel):
    id: str
    substance_id: str
    center_lat: float
    center_lon: float
    depth_min: float
    depth_max: float | None
    depth_mean: float | None
    confidence: float
    area_m2: float | None
    volume_m3: float | None
    geometry_json: str


class ResultSummary(BaseModel):
    job_id: str
    anomalies: list[AnomalyOut]
    substances_found: list[str]


class SpectrumResult(BaseModel):
    frequencies: list[float]
    amplitudes: list[float]
    peaks: list[dict]
    best_match: dict | None


class Model3DResult(BaseModel):
    vertices: list[list[float]]
    faces: list[list[int]]
    colors: list[list[float]]
    bounds: dict
    volume_m3: float | None
