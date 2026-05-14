"""Thompson sampling + posterior diagnostics, cohort-aware.

Each (variant, cohort) pair has its own Posterior row (alpha, beta). Posteriors
are lazy-created from the variant's seed alpha/beta when a cohort first sees
that variant.
"""
import numpy as np
from sqlalchemy.orm import Session

from eigen import models


def get_or_create_posterior(db: Session, variant: models.Variant, cohort: str) -> models.Posterior:
    p = (
        db.query(models.Posterior)
        .filter_by(variant_id=variant.id, cohort=cohort)
        .first()
    )
    if p is None:
        p = models.Posterior(
            variant_id=variant.id,
            cohort=cohort,
            alpha=variant.alpha,
            beta=variant.beta,
        )
        db.add(p)
        db.flush()
    return p


def sample_variant(
    db: Session,
    active_variants: list[models.Variant],
    cohort: str,
    rng: np.random.Generator | None = None,
) -> int:
    """Thompson sample. Returns chosen variant.id."""
    rng = rng or np.random.default_rng()
    if not active_variants:
        raise ValueError("no active variants")
    draws = []
    for v in active_variants:
        p = get_or_create_posterior(db, v, cohort)
        draws.append(rng.beta(p.alpha, p.beta))
    return active_variants[int(np.argmax(draws))].id


def prob_best(
    db: Session,
    active_variants: list[models.Variant],
    cohort: str,
    n_samples: int = 4000,
    rng: np.random.Generator | None = None,
) -> dict[int, float]:
    rng = rng or np.random.default_rng(0)
    if not active_variants:
        return {}
    posteriors = [get_or_create_posterior(db, v, cohort) for v in active_variants]
    draws = np.stack([rng.beta(p.alpha, p.beta, size=n_samples) for p in posteriors])
    winners = np.argmax(draws, axis=0)
    counts = np.bincount(winners, minlength=len(active_variants))
    probs = counts / n_samples
    return {v.id: float(p) for v, p in zip(active_variants, probs)}


def inherited_prior(parent_alpha: float, parent_beta: float, pseudo_count: float = 4.0) -> tuple[float, float]:
    mean = parent_alpha / (parent_alpha + parent_beta)
    a = max(1e-3, mean * pseudo_count)
    b = max(1e-3, (1.0 - mean) * pseudo_count)
    return a, b


def posterior_variance(alpha: float, beta: float) -> float:
    a, b = alpha, beta
    return (a * b) / ((a + b) ** 2 * (a + b + 1))


def samples_observed(alpha: float, beta: float) -> float:
    return (alpha - 1) + (beta - 1)
