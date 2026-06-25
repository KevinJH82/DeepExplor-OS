import os
os.environ["TZ"] = "Asia/Shanghai"

from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pathlib import Path

from .config import settings
from .db.session import init_db, SessionLocal
from .db.seed import seed_database
from .api.endpoints import uploads, jobs, results, spectral_library


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    db = SessionLocal()
    seed_database(db)
    db.close()
    Path(settings.UPLOAD_DIR).mkdir(parents=True, exist_ok=True)
    Path(settings.JOB_OUTPUT_DIR).mkdir(parents=True, exist_ok=True)
    yield


app = FastAPI(title=settings.APP_NAME, version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(uploads.router, prefix="/api/uploads", tags=["uploads"])
app.include_router(jobs.router, prefix="/api/jobs", tags=["jobs"])
app.include_router(results.router, prefix="/api/results", tags=["results"])
app.include_router(spectral_library.router, prefix="/api/library", tags=["library"])


@app.get("/api/health")
async def health():
    return {"status": "ok", "app": settings.APP_NAME}
