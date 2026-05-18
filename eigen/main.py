import logging
from contextlib import asynccontextmanager

from eigen.envloader import load as _load_dotenv

_load_dotenv()  # MUST run before any eigen.config import below

from fastapi import FastAPI  # noqa: E402

from eigen.admin import router as admin_router  # noqa: E402
from eigen.db import init_db  # noqa: E402
from eigen.routes import router  # noqa: E402
from eigen.webhooks import router as webhooks_router  # noqa: E402

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


@app.get("/")
def root():
    return {"service": "eigen-backend", "version": "0.0.1"}
