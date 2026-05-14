# eigen-backend

Prototype of the Eigen multi-armed bandit email-testing pipeline.

**Status:** smoke-screened end-to-end. Email sends are stubbed (logged, not delivered); the variant generator is a string mutator, not an LLM; events are ingested via plain HTTP.

## Stack

- FastAPI · SQLAlchemy · SQLite · NumPy
- Python 3.11+

## Architecture

Matches the three loops from the design doc:

```
Synchronous:  campaigns/{id}/tick  →  Batcher → Sampler (Thompson) → Dispatcher (stub) → Send
Dynamic:      POST /events         →  Updater → α/β on variant
Research:     campaigns/{id}/research → Generation Policy → spawn/kill variants
```

## Run

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e .
uvicorn eigen.main:app --reload
```

Then in another shell:

```bash
python scripts/simulate.py
```

The simulator drives a 3-variant campaign through 25 rounds of 200 sends, feeding back clicks at the hidden ground-truth CTR, and periodically runs `/research`. You should see `P(best)` collapse onto the best arm and weak arms get pruned.

## API

| Method | Path | What |
|---|---|---|
| POST | `/campaigns` | create campaign + initial variants (with hidden true_ctr for sim) |
| POST | `/campaigns/{id}/recipients` | bulk add recipients |
| POST | `/campaigns/{id}/tick?n=N` | sample + dispatch a batch of N |
| POST | `/events` | record event (e.g. click), updates posterior immediately |
| POST | `/campaigns/{id}/settle` | close un-clicked sends as failures (β += 1) |
| POST | `/campaigns/{id}/research` | run generation policy (kill/spawn) |
| GET | `/campaigns/{id}/state` | variants with α, β, mean, P(best), sends/clicks |

## Smoke-screened pieces (to be replaced)

- `eigen/dispatcher.py` — logs instead of sending email
- `eigen/policy.py` `run_research` — mutates parent subject string instead of calling an LLM
- `Campaign.true_ctrs` — ground truth for the simulator only; ignored at runtime
- No auth, no migrations, no async workers, no rate limiting

## Bandit math

- Conjugate Beta-Binomial: each variant has `α, β` (starts at `1, 1` — diffuse prior).
- Thompson sampling per-recipient: draw `θᵢ ~ Beta(αᵢ, βᵢ)`, send the argmax variant.
- Click → `α += 1`, settle window → `β += 1` for un-clicked sends.
- `P(best)` estimated by Monte Carlo over the posteriors (4k samples).
- Generation policy: kill variants with `P(best) < 0.05` after ≥100 samples; spawn a child from the leader when all survivors' posterior variance falls below threshold.
