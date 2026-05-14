import logging

from fastapi import FastAPI

from eigen.db import init_db
from eigen.routes import router

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

app = FastAPI(title="Eigen Backend", version="0.0.1")
app.include_router(router)


@app.on_event("startup")
def _startup():
    init_db()


@app.get("/")
def root():
    return {"service": "eigen-backend", "version": "0.0.1"}
