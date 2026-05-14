"""Statistical regression suite.

Slow. Marked statistical — run with: pytest -m statistical
"""
import numpy as np
import pytest

from eigen.sim import regret, run_sim, thompson_sample, uniform_sample

pytestmark = pytest.mark.statistical


def test_regret_thompson_beats_uniform():
    """Over 200 sims, average Thompson regret should be substantially lower than uniform."""
    true_ctrs = [0.02, 0.04, 0.06, 0.09]
    oracle = max(true_ctrs)
    n_pulls = 2000

    t_regrets, u_regrets = [], []
    for seed in range(200):
        r_t = run_sim(true_ctrs, n_pulls, strategy=thompson_sample, seed=seed)
        r_u = run_sim(true_ctrs, n_pulls, strategy=uniform_sample, seed=seed)
        t_regrets.append(regret(r_t, oracle))
        u_regrets.append(regret(r_u, oracle))

    mean_t = float(np.mean(t_regrets))
    mean_u = float(np.mean(u_regrets))
    improvement = (mean_u - mean_t) / mean_u
    print(f"\nThompson regret={mean_t:.1f} vs uniform={mean_u:.1f} ({improvement:.1%} better)")
    assert improvement > 0.30, f"Thompson only {improvement:.1%} better — expected >30%"


def test_aa_no_spurious_winner():
    """When all arms have identical CTR, no arm should be declared winner abnormally often."""
    n_arms = 4
    true_ctrs = [0.05] * n_arms
    n_pulls = 5000
    sims = 200

    winners = np.zeros(n_arms, dtype=int)
    for seed in range(sims):
        r = run_sim(true_ctrs, n_pulls, strategy=thompson_sample, seed=seed)
        # winner = highest empirical mean
        idx = int(np.argmax([a.alpha / (a.alpha + a.beta) for a in r.final_arms]))
        winners[idx] += 1

    # Each arm should win roughly 1/n of the time. Allow 2x slack.
    expected = sims / n_arms
    for i, w in enumerate(winners):
        assert w < 2 * expected, f"arm {i} won {w}/{sims} (expected ~{expected:.0f})"


def test_posterior_coverage():
    """The 95% credible interval should contain the true CTR ~95% of the time."""
    # Approximation: at high sample count, Beta(a,b) ~ Normal(mu, sigma^2)
    # 95% CI = mu +/- 1.96*sigma. Check coverage.
    true_ctrs = [0.05]
    n_pulls = 3000

    covered = 0
    sims = 200
    for seed in range(sims):
        r = run_sim(true_ctrs, n_pulls, strategy=thompson_sample, seed=seed)
        arm = r.final_arms[0]
        a, b = arm.alpha, arm.beta
        mean = a / (a + b)
        var = (a * b) / ((a + b) ** 2 * (a + b + 1))
        sd = var ** 0.5
        lo, hi = mean - 1.96 * sd, mean + 1.96 * sd
        if lo <= true_ctrs[0] <= hi:
            covered += 1

    coverage = covered / sims
    print(f"\nCoverage: {coverage:.2%} (expected ~95%)")
    assert coverage >= 0.90, f"coverage {coverage:.2%} too low (expected >=90%)"


def test_spawn_beats_no_spawn_on_heterogeneous_landscape():
    """Auto-research should improve reward when the baseline mean is below the
    achievable ceiling (i.e. when there's room above the seed arm)."""
    # Baseline mean is 0.05 but jitter can find arms up to ~0.09.
    seed_ctr = 0.05
    sims = 100
    n_pulls = 3000

    no_spawn = []
    with_spawn = []
    for seed in range(sims):
        # Without spawn: stuck with the one arm.
        r0 = run_sim([seed_ctr], n_pulls, strategy=thompson_sample, seed=seed)
        no_spawn.append(r0.total_reward)
        # With spawn: jittered children, some hit higher true CTR.
        r1 = run_sim(
            [seed_ctr], n_pulls, strategy=thompson_sample, seed=seed,
            spawn_after=200, spawn_jitter=0.04,
        )
        with_spawn.append(r1.total_reward)

    mean_no = float(np.mean(no_spawn))
    mean_with = float(np.mean(with_spawn))
    lift = (mean_with - mean_no) / mean_no
    print(f"\nNo-spawn={mean_no:.1f}  With-spawn={mean_with:.1f}  lift={lift:.1%}")
    assert mean_with > mean_no, "spawn did not improve over single-arm baseline"
