"""Thompson sampling + posterior diagnostics."""
import numpy as np


def sample_variant(variants: list, rng: np.random.Generator | None = None) -> int:
    """Thompson sample: draw from each active variant's Beta(alpha, beta), return chosen variant.id."""
    rng = rng or np.random.default_rng()
    active = [v for v in variants if v.status == "active"]
    if not active:
        raise ValueError("no active variants")
    draws = [rng.beta(v.alpha, v.beta) for v in active]
    return active[int(np.argmax(draws))].id


def prob_best(variants: list, n_samples: int = 4000, rng: np.random.Generator | None = None) -> dict[int, float]:
    """Estimate P(variant is best) by Monte Carlo over the posteriors."""
    rng = rng or np.random.default_rng(0)
    active = [v for v in variants if v.status == "active"]
    if not active:
        return {}
    draws = np.stack([rng.beta(v.alpha, v.beta, size=n_samples) for v in active])  # (k, n)
    winners = np.argmax(draws, axis=0)
    counts = np.bincount(winners, minlength=len(active))
    probs = counts / n_samples
    return {v.id: float(p) for v, p in zip(active, probs)}


def posterior_variance(alpha: float, beta: float) -> float:
    a, b = alpha, beta
    return (a * b) / ((a + b) ** 2 * (a + b + 1))


def is_stabilized(variant, var_threshold: float = 1e-4, min_samples: int = 100) -> bool:
    n = (variant.alpha - 1) + (variant.beta - 1)
    return n >= min_samples and posterior_variance(variant.alpha, variant.beta) < var_threshold
