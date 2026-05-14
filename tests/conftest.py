"""Shared test fixtures."""
import os
import tempfile

import pytest

# Configure a fresh SQLite DB per test session before importing eigen.*
_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()
os.environ["EIGEN_DATABASE_URL"] = f"sqlite:///{_tmp.name}"
os.environ["EIGEN_ESP"] = "fake"
# Force dev-mode auth (open). The repo's .env exports API keys for local
# dev, which would otherwise leak into the test process and break every
# test that doesn't pass a Bearer header.
os.environ["EIGEN_API_KEYS"] = "[]"

from fastapi.testclient import TestClient  # noqa: E402

from eigen.config import settings  # noqa: E402
from eigen.db import init_db  # noqa: E402
from eigen.esp.fake import FakeDispatcher  # noqa: E402
from eigen.main import app  # noqa: E402

settings.cache_clear()


@pytest.fixture(autouse=True)
def _reset_state():
    # Re-init schema before each test
    FakeDispatcher.reset()
    # Drop and recreate
    from eigen.db import Base, engine

    Base.metadata.drop_all(bind=engine)
    init_db()
    yield


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture
def fake() -> FakeDispatcher:
    return FakeDispatcher.get()
