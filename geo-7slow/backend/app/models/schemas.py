"""Pydantic请求/响应模型"""
from typing import Optional
from pydantic import BaseModel


class AnalysisParams(BaseModel):
    weights: Optional[dict] = None
    delta_threshold: Optional[float] = None
    target_resolution: Optional[float] = None
    gaussian_sigma: Optional[float] = None
    # 矿床类型预设(自动接口):传入则按矿床族解析权重预设;显式 weights 优先
    deposit_type: Optional[str] = None
    family: Optional[str] = None
    mineral: Optional[str] = None
    commodity: Optional[str] = None
    aoi_name: Optional[str] = None
    project: Optional[str] = None
    trace_id: Optional[str] = None
    geologic_context: Optional[dict] = None


class AnalysisRequest(BaseModel):
    upload_id: str
    params: Optional[AnalysisParams] = None


class FileMeta(BaseModel):
    slot: str
    filename: str
    crs: Optional[str] = None
    resolution: Optional[float] = None
    width: Optional[int] = None
    height: Optional[int] = None
    bounds: Optional[list] = None


class UploadResponse(BaseModel):
    upload_id: str
    files: list[FileMeta]


class TaskProgress(BaseModel):
    task_id: str
    status: str  # queued, running, completed, failed
    progress: float  # 0-100
    current_step: str
    error: Optional[str] = None
    results: Optional[dict] = None


class LayerInfo(BaseModel):
    name: str
    title: str
    tile_url: str
    stats: Optional[dict] = None


class SlotMatch(BaseModel):
    slot: str
    original_filename: str
    meta: FileMeta


class SlotInfo(BaseModel):
    slot: str
    label: str
    required: bool


class SlotMatchError(BaseModel):
    slot: str
    original_filename: str
    error: str


class ZipUploadResponse(BaseModel):
    upload_id: str
    matched: list[SlotMatch]
    unmatched: list[SlotInfo]
    errors: list[SlotMatchError]
    required_missing: list[str]
    all_required_filled: bool
    warnings: list[str] = []
