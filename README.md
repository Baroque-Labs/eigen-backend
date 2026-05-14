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
| POST | `/campaigns` | create campaign: `{name, baseline, n_variants, n_batches, emails[]}` — server seeds `n_variants - 1` children from the baseline with inherited priors |
| POST | `/campaigns/{id}/tick` | sample + dispatch one batch (batch size derived from `len(emails)/n_batches`) |
| POST | `/events` | record an event (e.g. click), updates posterior immediately |
| POST | `/campaigns/{id}/settle?window_seconds=N` | settle sends older than N seconds as failures (β += 1) |
| POST | `/campaigns/{id}/research` | run generation policy (kill/spawn) |
| GET | `/campaigns/{id}/state` | variants with α, β, mean, P(best), sends/clicks |
| GET | `/campaigns/{id}/_truth` | **smoke-screen**: hidden ground-truth CTRs for the simulator |

## Smoke-screened pieces (to be replaced)

- `eigen/dispatcher.py` — logs instead of sending email
- `eigen/policy.py` `run_research` — mutates parent subject string instead of calling an LLM
- `Campaign.true_ctrs` — ground truth for the simulator only; ignored at runtime
- No auth, no migrations, no async workers, no rate limiting

## Bandit math

- Conjugate Beta-Binomial: each variant has `α, β`.
- **Baseline** starts at `Beta(1, 1)` — diffuse prior.
- **Spawned variants** start at `Beta(μ·k, (1−μ)·k)` with `k=4` (inherited prior centered on parent's posterior mean; gets washed out by real data within ~few dozen samples).
- Thompson sampling per-recipient: draw `θᵢ ~ Beta(αᵢ, βᵢ)`, send the argmax variant.
- Click → `α += 1`; sends older than the settle window with no click → `β += 1`.
- `P(best)` estimated by Monte Carlo over the posteriors (4k samples).
- Generation policy:
  - **Kill** any non-leader with `P(best) < 0.05` after ≥200 samples. The current lifetime leader (highest posterior mean among variants with ≥200 samples) is immune.
  - **Spawn** children from the current best survivor until active variant count == `n_variants`.

## Heads up

This is a prototype; no migrations. If you change the schema, delete `eigen.db` and restart.
