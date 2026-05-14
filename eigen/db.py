from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from eigen.config import settings


def _engine_kwargs(url: str) -> dict:
    if url.startswith("sqlite"):
        return {"connect_args": {"check_same_thread": False}}
    return {"pool_pre_ping": True}


engine = create_engine(settings().database_url, **_engine_kwargs(settings().database_url))
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """Used in tests / dev only. Production uses Alembic migrations."""
    from eigen import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
