"""Slim multi-tenancy: Org + API keys.

In dev (no EIGEN_API_KEYS set, no ApiKey rows), the first request transparently
creates a 'default' org and uses it for everything. As soon as a real API key
exists in the DB or env, dev mode turns off and auth is required.
"""
import hashlib
import secrets

from fastapi import Depends, Header, HTTPException
from sqlalchemy.orm import Session

from eigen import models
from eigen.db import get_db


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def mint_key() -> str:
    """Returns a fresh raw key. Caller hashes it before storing."""
    return f"ek_{secrets.token_urlsafe(32)}"


def _ensure_default_org(db: Session) -> models.Org:
    org = db.query(models.Org).filter_by(name="default").first()
    if not org:
        org = models.Org(name="default")
        db.add(org)
        db.commit()
    return org


def require_org(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> models.Org:
    """Resolve auth header to an Org. Falls back to a default org in dev mode."""
    from eigen.config import settings

    configured_keys = settings().api_keys
    db_keys_count = db.query(models.ApiKey).count()
    dev_mode = not configured_keys and db_keys_count == 0

    if dev_mode:
        return _ensure_default_org(db)

    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "missing bearer token")
    raw = authorization.split(None, 1)[1].strip()
    h = hash_key(raw)

    # Env-configured keys all map to default org for simplicity.
    if raw in configured_keys:
        return _ensure_default_org(db)

    key = db.query(models.ApiKey).filter_by(key_hash=h).first()
    if not key:
        raise HTTPException(401, "invalid api key")
    return db.get(models.Org, key.org_id)
