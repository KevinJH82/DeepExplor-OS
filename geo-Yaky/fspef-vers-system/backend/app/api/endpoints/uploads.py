"""Upload endpoints."""
import uuid
import shutil
from pathlib import Path
from fastapi import APIRouter, UploadFile, File, Form, HTTPException
from sqlalchemy.orm import Session
from fastapi import Depends

from ...models.substance import Upload
from ...schemas.schemas import UploadOut
from ...db.session import get_db
from ...config import settings

router = APIRouter()


@router.post("/", response_model=UploadOut)
async def create_upload(
    file: UploadFile = File(...),
    survey_type: str = Form("regional"),
    db: Session = Depends(get_db),
):
    ext = Path(file.filename).suffix.lower()
    if ext not in {".csv", ".tif", ".tiff", ".png", ".jpg", ".json"}:
        raise HTTPException(400, f"Unsupported file type: {ext}")

    upload_id = str(uuid.uuid4())
    upload_dir = Path(settings.UPLOAD_DIR)
    upload_dir.mkdir(parents=True, exist_ok=True)
    file_path = upload_dir / f"{upload_id}{ext}"

    with open(file_path, "wb") as f:
        content = await file.read()
        if len(content) > settings.MAX_UPLOAD_SIZE:
            raise HTTPException(400, "File too large")
        f.write(content)

    file_type = ext.lstrip(".")
    if file_type in ("tif", "tiff"):
        file_type = "geotiff"

    upload = Upload(
        id=upload_id,
        filename=file.filename,
        file_type=file_type,
        file_path=str(file_path),
        file_size=len(content),
        survey_type=survey_type,
    )
    db.add(upload)
    db.commit()
    db.refresh(upload)
    return upload


@router.get("/{upload_id}", response_model=UploadOut)
async def get_upload(upload_id: str, db: Session = Depends(get_db)):
    upload = db.query(Upload).filter(Upload.id == upload_id).first()
    if not upload:
        raise HTTPException(404, "Upload not found")
    return upload
