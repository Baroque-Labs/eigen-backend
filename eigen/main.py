import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from eigen.admin import router as admin_router
from eigen.config import settings
from eigen.db import init_db
from eigen.inbox import router as inbox_router
from eigen.routes import router
from eigen.webhooks import router as webhooks_router

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Create any missing tables on boot. We dropped Alembic — schema lives in
    # eigen/models.py and propagates via SQLAlchemy's create_all. If you need
    # to evolve an existing column type, drop the DB and let it recreate.
    init_db()
    yield


app = FastAPI(title="Eigen Backend", version="0.0.1", lifespan=lifespan)
app.include_router(router)
app.include_router(webhooks_router)
app.include_router(admin_router)
if settings().esp in ("fake", "log"):
    app.include_router(inbox_router)


@app.get("/")
def root():
    return {"service": "eigen-backend", "version": "0.0.1"}
