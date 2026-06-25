import uuid
from sqlalchemy import String, Float, Text, DateTime, ForeignKey, Integer
from sqlalchemy.orm import Mapped, mapped_column, relationship
from datetime import datetime
from ..db.session import Base


class Substance(Base):
    __tablename__ = "substances"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    freq_min: Mapped[float] = mapped_column(Float, nullable=False)
    freq_max: Mapped[float] = mapped_column(Float, nullable=False)
    c_equivalent: Mapped[float] = mapped_column(Float, default=300.0)
    threshold: Mapped[float] = mapped_column(Float, default=0.75)
    color: Mapped[str] = mapped_column(String(7), nullable=False)
    icon: Mapped[str | None] = mapped_column(String(50))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)

    references: Mapped[list["SpectralReference"]] = relationship(back_populates="substance")


class SpectralReference(Base):
    __tablename__ = "spectral_references"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    substance_id: Mapped[str] = mapped_column(String(32), ForeignKey("substances.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str | None] = mapped_column(String(100))
    source_url: Mapped[str | None] = mapped_column(String(500))
    freq_data: Mapped[str] = mapped_column(Text, nullable=False)  # JSON array of frequencies
    amp_data: Mapped[str] = mapped_column(Text, nullable=False)   # JSON array of amplitudes
    n_points: Mapped[int] = mapped_column(Integer, nullable=False)
    freq_min: Mapped[float] = mapped_column(Float, nullable=False)
    freq_max: Mapped[float] = mapped_column(Float, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)

    substance: Mapped["Substance"] = relationship(back_populates="references")


class Upload(Base):
    __tablename__ = "uploads"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    file_type: Mapped[str] = mapped_column(String(20), nullable=False)
    file_path: Mapped[str] = mapped_column(String(500), nullable=False)
    file_size: Mapped[int | None] = mapped_column(Integer)
    survey_type: Mapped[str | None] = mapped_column(String(20))
    lat_min: Mapped[float | None] = mapped_column(Float)
    lat_max: Mapped[float | None] = mapped_column(Float)
    lon_min: Mapped[float | None] = mapped_column(Float)
    lon_max: Mapped[float | None] = mapped_column(Float)
    metadata_json: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)

    jobs: Mapped[list["Job"]] = relationship(back_populates="upload")


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    upload_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("uploads.id"))
    status: Mapped[str] = mapped_column(String(20), default="queued")
    current_stage: Mapped[int] = mapped_column(Integer, default=0)
    percent: Mapped[float] = mapped_column(Float, default=0.0)
    target_substances: Mapped[str] = mapped_column(Text, default="[]")  # JSON array
    parameters_json: Mapped[str] = mapped_column(Text, default="{}")
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)

    upload: Mapped["Upload | None"] = relationship(back_populates="jobs")
    results: Mapped[list["AnalysisResult"]] = relationship(back_populates="job")
    anomalies: Mapped[list["Anomaly"]] = relationship(back_populates="job")


class AnalysisResult(Base):
    __tablename__ = "analysis_results"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    job_id: Mapped[str] = mapped_column(String(36), ForeignKey("jobs.id"), nullable=False)
    substance_id: Mapped[str | None] = mapped_column(String(32), ForeignKey("substances.id"))
    result_type: Mapped[str] = mapped_column(String(20), nullable=False)
    file_path: Mapped[str | None] = mapped_column(String(500))
    data_json: Mapped[str | None] = mapped_column(Text)
    summary_json: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)

    job: Mapped["Job"] = relationship(back_populates="results")


class Anomaly(Base):
    __tablename__ = "anomalies"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    job_id: Mapped[str] = mapped_column(String(36), ForeignKey("jobs.id"), nullable=False)
    substance_id: Mapped[str] = mapped_column(String(32), ForeignKey("substances.id"), nullable=False)
    center_lat: Mapped[float] = mapped_column(Float, nullable=False)
    center_lon: Mapped[float] = mapped_column(Float, nullable=False)
    depth_min: Mapped[float] = mapped_column(Float, nullable=False)
    depth_max: Mapped[float | None] = mapped_column(Float)
    depth_mean: Mapped[float | None] = mapped_column(Float)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    area_m2: Mapped[float | None] = mapped_column(Float)
    volume_m3: Mapped[float | None] = mapped_column(Float)
    geometry_json: Mapped[str] = mapped_column(Text, nullable=False)
    properties_json: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)

    job: Mapped["Job"] = relationship(back_populates="anomalies")
