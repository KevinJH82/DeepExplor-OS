from pydantic_settings import BaseSettings
from pathlib import Path


class Settings(BaseSettings):
    APP_NAME: str = "FSPEF-VERS 频率共振分析系统"
    DEBUG: bool = True
    DATABASE_URL: str = f"sqlite:///{Path(__file__).parent.parent / 'fspef_vers.db'}"
    UPLOAD_DIR: str = str(Path(__file__).parent / "static" / "uploads")
    JOB_OUTPUT_DIR: str = str(Path(__file__).parent / "static" / "jobs")
    MAX_UPLOAD_SIZE: int = 100 * 1024 * 1024  # 100MB
    CORS_ORIGINS: list[str] = ["http://localhost:5188", "http://127.0.0.1:5188", "http://localhost:5173", "http://127.0.0.1:5173"]

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()
