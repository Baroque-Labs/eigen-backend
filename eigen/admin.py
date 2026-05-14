"""Admin endpoints for org/key management. Guarded by an env-only master key
(EIGEN_API_KEYS — any of those values authenticates against /admin/*).
"""
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from eigen import models
from eigen.auth import hash_key, mint_key
from eigen.config import settings
from eigen.db import get_db

router = APIRouter(prefix="/admin", tags=["admin"])


def require_master(authorization: str | None = Header(default=None)) -> None:
    keys = settings().api_keys
    if not keys:
        # dev mode — admin is open
        return
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "missing bearer token")
    raw = authorization.split(None, 1)[1].strip()
    if raw not in keys:
        raise HTTPException(403, "admin requires a master EIGEN_API_KEYS value")


class OrgIn(BaseModel):
    name: str


@router.post("/orgs", dependencies=[Depends(require_master)])
def create_org(payload: OrgIn, db: Session = Depends(get_db)):
    if db.query(models.Org).filter_by(name=payload.name).first():
        raise HTTPException(409, "org name taken")
    org = models.Org(name=payload.name)
    db.add(org)
    db.commit()
    return {"id": org.id, "name": org.name}


class KeyIn(BaseModel):
    org_id: int
    label: str = ""


@router.post("/keys", dependencies=[Depends(require_master)])
def create_key(payload: KeyIn, db: Session = Depends(get_db)):
    if not db.get(models.Org, payload.org_id):
        raise HTTPException(404, "org not found")
    raw = mint_key()
    db.add(models.ApiKey(org_id=payload.org_id, key_hash=hash_key(raw), label=payload.label))
    db.commit()
    # Only time we return the raw key — caller must save it.
    return {"api_key": raw, "label": payload.label}
