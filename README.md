# eigen-backend

Production-shaped implementation of the Eigen multi-armed bandit email-testing pipeline.

## Stack

- **Web:** FastAPI · SQLAlchemy 2 · pydantic-settings
- **Storage:** Postgres (prod) / SQLite (dev & tests). Alembic for migrations.
- **Queue:** arq + Redis. The same worker also runs cron-scheduled tick/settle/research.
- **ESP:** Resend (real) · `LogDispatcher` (stdout, dev) · `FakeDispatcher` (in-process, tests).
- **LLM:** Anthropic SDK (`messages.parse()` with a pydantic schema for structured output) for the variant generator. Template generator as a smoke-screen fallback.
- **Auth:** slim org + API-key model with master-env-key admin endpoints.

## Architecture

```
                    ┌──────────────────┐
                    │   FastAPI app    │  /campaigns, /events, /webhooks/*, /admin/*
                    └────────┬─────────┘
                             │
         ┌───────────────────┼────────────────────┐
         ▼                   ▼                    ▼
   ┌──────────┐        ┌──────────┐         ┌──────────┐
   │ Postgres │        │  Redis   │         │   ESP    │  (Resend / Fake / Log)
   │          │        │  + arq   │         │          │
   └──────────┘        └────┬─────┘         └────▲─────┘
                            │                    │
                            ▼                    │
                    ┌──────────────┐             │
                    │ arq worker   │─────────────┘
                    │ - dispatch   │   async send jobs
                    │ - cron tick  │
                    │ - cron settle│
                    │ - cron research
                    └──────────────┘
```

## Data model

| Table | What it is |
|---|---|
| `org`, `api_key` | Tenancy + auth. |
| `campaign` | One A/B test. Carries `status: running\|stopped`, `n_variants`, `batch_size`. |
| `variant` | Subject + body. `status: active\|pending\|killed\|rejected`. Seed `alpha`/`beta` used to initialise per-cohort posteriors. |
| `posterior` | `(variant_id, cohort) → α, β`. The **live** posterior the sampler reads. |
| `recipient` | Email + cohort tag. |
| `send` | One dispatched email. Carries `provider_message_id`, `cohort`, `settled_at`. |
| `event` | Webhook event with `(provider, provider_event_id)` unique for idempotency. |
| `suppression` | Per-org list of permanently-suppressed addresses. |
| `decision` | Audit log of every kill/spawn/stop with posterior snapshot. |

## API

| Method | Path | Notes |
|---|---|---|
| POST | `/campaigns` | `{name, baseline, n_variants, n_batches, emails[] or recipients[]}` |
| POST | `/campaigns/{id}/tick` | one batch. Sync or async dispatch per `EIGEN_SEND_MODE`. |
| POST | `/events` | manual event (legacy path; webhooks are the real channel) |
| POST | `/webhooks/resend` | Svix-HMAC verified |
| POST | `/webhooks/fake` | tests only |
| POST | `/campaigns/{id}/settle?window_seconds=N` | β += 1 for un-clicked sends older than N seconds |
| POST | `/campaigns/{id}/research` | kill/spawn/stop pass; idempotent |
| GET | `/campaigns/{id}/pending` | variants awaiting approval |
| POST | `/campaigns/{id}/variants/{vid}/approve\|reject` | toggle pending → active/rejected |
| GET | `/campaigns/{id}/state` | per-variant, per-cohort posteriors + `P(best)` |
| GET | `/campaigns/{id}/decisions` | time-ordered audit log |
| POST | `/admin/orgs`, `/admin/keys` | gated by `EIGEN_API_KEYS` master env keys |

## Run

Postgres lives on Railway — there is no local DB. Grab the public connection
string once:

```bash
railway link --project eigen
railway service link Postgres
railway variables --kv | grep DATABASE_PUBLIC_URL
```

Then:

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e .
cp .env.example .env           # paste the Railway URL into EIGEN_DATABASE_URL

uvicorn eigen.main:app --reload     # schema auto-created on first boot

# Worker (in another shell, optional — only needed if EIGEN_SCHEDULER_ENABLED=true)
arq eigen.worker.WorkerSettings
```

Redis for arq still runs locally on the default `redis://localhost:6379/0` — install it via your package manager or skip the worker. (We can move Redis to Railway too if the scheduler ever runs in prod.)

## Configuration

All env vars are `EIGEN_`-prefixed. See `.env.example` for the full list. Key knobs:

| Var | What |
|---|---|
| `EIGEN_DATABASE_URL` | `postgresql+psycopg://...` or `sqlite:///./eigen.db` |
| `EIGEN_REDIS_URL` | arq broker |
| `EIGEN_ESP` | `log\|resend\|fake` |
| `EIGEN_RESEND_API_KEY`, `_WEBHOOK_SECRET` | required when `ESP=resend` |
| `EIGEN_GENERATOR` | `template\|llm` |
| `EIGEN_ANTHROPIC_API_KEY`, `_LLM_MODEL` | required when `GENERATOR=llm` |
| `EIGEN_AUTO_SPAWN` | if false, newly generated variants land in `pending` |
| `EIGEN_SEND_MODE` | `sync` (inline) or `async` (queue to arq) |
| `EIGEN_SCHEDULER_ENABLED` | per-tick gate inside the cron tasks |
| `EIGEN_SETTLE_WINDOW_SECONDS` | default 24h |
| `EIGEN_STOP_PROB_BEST` | stopping threshold; default 0.95 |
| `EIGEN_API_KEYS` | comma-separated master keys; empty = dev-mode open |

## Tests

```bash
pytest                          # fast (~1.5s) — unit + integration + auth + cohorts + stopping
pytest -m statistical           # Monte-Carlo bandit regression suite (~15s, 200-1000 sims each)
```

The statistical suite is the load-bearing one for bandit correctness:
- `test_regret_thompson_beats_uniform` — Thompson regret 30%+ lower than uniform over 200 sims
- `test_aa_no_spurious_winner` — identical-arms control, no arm wins abnormally often
- `test_posterior_coverage` — 95% credible interval covers true CTR ≥90% of runs
- `test_spawn_beats_no_spawn_on_heterogeneous_landscape` — auto-research outperforms a single-arm baseline

## Bandit specifics

- **Posterior model:** Conjugate Beta-Binomial. Each `(variant, cohort)` has its own α, β.
- **Sampling:** Thompson — per recipient, draw `θᵢ ~ Beta(αᵢ, βᵢ)` from each active variant's posterior *for this recipient's cohort*, send the argmax.
- **Update:** click → `α += 1`; sends older than the settle window with no click → `β += 1`.
- **Inherited prior on spawned variants:** `Beta(μ·k, (1−μ)·k)` with `k=4`, centered on the parent's posterior mean. Diffuse enough that real data dominates within ~40 samples.
- **Generation policy:**
  - **Kill** non-leaders with `P(best) < 0.05` across *all* eligible cohorts (≥200 samples). The current per-cohort leader is protected. A niche-cohort winner survives a global loss.
  - **Spawn** children from the leader until active count == `n_variants`.
  - **Stop** when there's a clear leader (`P(best) > EIGEN_STOP_PROB_BEST`) in every cohort with enough samples.

## Heads up

This is a private prototype. There are no migrations — schema lives in `eigen/models.py` and SQLAlchemy's `Base.metadata.create_all` runs on startup, which only **adds** missing tables/columns. If you need to change a column type or rename, drop the affected database (`psql $EIGEN_DATABASE_URL -c "DROP TABLE <name>"` or recreate the DB) and let it recreate on next boot. Add Alembic back if you ever need to preserve data across migrations.
