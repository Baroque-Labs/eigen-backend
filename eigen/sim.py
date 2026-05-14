"""In-process bandit simulator. No HTTP, no DB — pure Python for stats tests.

Mirrors the math in eigen.bandit and eigen.policy but operates on plain dicts
so the suite runs fast enough to do 1000 sims per test.
"""
import math
import random
from dataclasses import dataclass, field

import numpy as np


@dataclass
class Arm:
    true_ctr: float
    alpha: float = 1.0
    beta: float = 1.0
    pulls: int = 0
    rewards: int = 0
    active: bool = True


@dataclass
class SimResult:
    final_arms: list[Arm]
    total_reward: int
    pulls: int
    history_best_arm_idx: list[int] = field(default_factory=list)


def thompson_sample(arms: list[Arm], rng: np.random.Generator) -> int:
    active_idx = [i for i, a in enumerate(arms) if a.active]
    draws = [rng.beta(arms[i].alpha, arms[i].beta) for i in active_idx]
    return active_idx[int(np.argmax(draws))]


def uniform_sample(arms: list[Arm], rng: np.random.Generator) -> int:
    active_idx = [i for i, a in enumerate(arms) if a.active]
    return rng.choice(active_idx)


def run_sim(
    true_ctrs: list[float],
    n_pulls: int,
    strategy=thompson_sample,
    seed: int = 0,
    spawn_after: int | None = None,
    spawn_jitter: float = 0.04,
) -> SimResult:
    """Run a single simulation. `strategy` is a function (arms, rng) -> arm_idx."""
    rng = np.random.default_rng(seed)
    arms = [Arm(true_ctr=c) for c in true_ctrs]
    total_reward = 0
    for t in range(n_pulls):
        idx = strategy(arms, rng)
        arm = arms[idx]
        reward = 1 if rng.random() < arm.true_ctr else 0
        arm.pulls += 1
        arm.rewards += reward
        arm.alpha += reward
        arm.beta += 1 - reward
        total_reward += reward

        # Auto-research: spawn a child from the current leader every spawn_after pulls.
        if spawn_after and t > 0 and t % spawn_after == 0:
            leader = max(
                (a for a in arms if a.active),
                key=lambda a: a.alpha / (a.alpha + a.beta),
            )
            child_ctr = max(0.0, min(1.0, leader.true_ctr + rng.uniform(-spawn_jitter, spawn_jitter)))
            mean = leader.alpha / (leader.alpha + leader.beta)
            arms.append(
                Arm(
                    true_ctr=child_ctr,
                    alpha=max(1e-3, mean * 4.0),
                    beta=max(1e-3, (1 - mean) * 4.0),
                )
            )

    return SimResult(final_arms=arms, total_reward=total_reward, pulls=n_pulls)


def regret(result: SimResult, oracle_ctr: float) -> float:
    return oracle_ctr * result.pulls - result.total_reward
