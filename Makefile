.PHONY: dev worker test install seed clean

VENV := .venv
UVICORN := $(VENV)/bin/uvicorn
PYTEST := $(VENV)/bin/pytest
ARQ := $(VENV)/bin/arq
PIP := $(VENV)/bin/pip

# `make dev` — run the FastAPI server on http://localhost:8000 with autoreload.
# Reads .env via pydantic-settings.
dev: $(UVICORN)
	$(UVICORN) eigen.main:app --reload --host 0.0.0.0 --port 8000

# `make worker` — run the arq worker (cron tick/settle/research + async dispatch).
worker: $(ARQ)
	$(ARQ) eigen.worker.WorkerSettings

# `make test` — unit + integration suite (excludes slow statistical sims).
test: $(PYTEST)
	$(PYTEST) tests/ -q

# `make seed` — create a demo campaign with the sample recipient list and run
# two ticks so the inbox has rows immediately. Backend must be running.
seed: $(VENV)/bin/python
	$(VENV)/bin/python scripts/seed_demo.py

# `make install` — create .venv and editable-install the package.
install: $(VENV)/bin/python
	$(PIP) install -e .

$(VENV)/bin/python:
	python -m venv $(VENV)
	$(PIP) install -e .

$(UVICORN) $(PYTEST) $(ARQ): | $(VENV)/bin/python

clean:
	rm -rf $(VENV) *.egg-info
