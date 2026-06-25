"""Spectral library endpoints."""
import json
from fastapi import APIRouter, HTTPException, Depends, Query
from sqlalchemy.orm import Session

from ...models.substance import Substance, SpectralReference
from ...schemas.schemas import SubstanceOut, SpectralRefOut, SpectralRefCreate
from ...db.session import get_db
from ...db.seed import seed_database

router = APIRouter()


@router.get("/substances", response_model=list[SubstanceOut])
async def list_substances(db: Session = Depends(get_db)):
    seed_database(db)
    return db.query(Substance).all()


@router.get("/references", response_model=list[SpectralRefOut])
async def list_references(substance: str | None = None, db: Session = Depends(get_db)):
    query = db.query(SpectralReference)
    if substance:
        query = query.filter(SpectralReference.substance_id == substance)
    refs = query.all()
    result = []
    for ref in refs:
        result.append(SpectralRefOut(
            id=ref.id,
            substance_id=ref.substance_id,
            name=ref.name,
            description=ref.description,
            source=ref.source,
            freq_data=json.loads(ref.freq_data),
            amp_data=json.loads(ref.amp_data),
            n_points=ref.n_points,
            freq_min=ref.freq_min,
            freq_max=ref.freq_max,
        ))
    return result


@router.get("/references/{ref_id}", response_model=SpectralRefOut)
async def get_reference(ref_id: str, db: Session = Depends(get_db)):
    ref = db.query(SpectralReference).filter(SpectralReference.id == ref_id).first()
    if not ref:
        raise HTTPException(404, "Reference not found")
    return SpectralRefOut(
        id=ref.id,
        substance_id=ref.substance_id,
        name=ref.name,
        description=ref.description,
        source=ref.source,
        freq_data=json.loads(ref.freq_data),
        amp_data=json.loads(ref.amp_data),
        n_points=ref.n_points,
        freq_min=ref.freq_min,
        freq_max=ref.freq_max,
    )


@router.post("/references", response_model=SpectralRefOut)
async def create_reference(data: SpectralRefCreate, db: Session = Depends(get_db)):
    ref = SpectralReference(
        substance_id=data.substance_id,
        name=data.name,
        description=data.description,
        source=data.source,
        freq_data=json.dumps(data.freq_data),
        amp_data=json.dumps(data.amp_data),
        n_points=len(data.freq_data),
        freq_min=min(data.freq_data),
        freq_max=max(data.freq_data),
    )
    db.add(ref)
    db.commit()
    db.refresh(ref)
    return SpectralRefOut(
        id=ref.id,
        substance_id=ref.substance_id,
        name=ref.name,
        description=ref.description,
        source=ref.source,
        freq_data=json.loads(ref.freq_data),
        amp_data=json.loads(ref.amp_data),
        n_points=ref.n_points,
        freq_min=ref.freq_min,
        freq_max=ref.freq_max,
    )
