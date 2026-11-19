"""Statistics of ADR-0003 section 7.2: a configuration's score is the mean over questions of the per-question mean over
runs; its 95% confidence interval comes from resampling the questions (bootstrap, 10,000 times, fixed seed); two
configurations are compared paired, question by question, on the same questions.
"""
import math
import random

RESAMPLES = 10_000


def per_question(scores: dict) -> dict:
    """{question id: [score of each run]} -> {question id: mean over its runs}."""
    return {q: sum(v) / len(v) for q, v in scores.items() if v}


def mean(xs) -> float:
    xs = list(xs)
    return sum(xs) / len(xs) if xs else float("nan")


def bootstrap_ci(values, level: float = 0.95, resamples: int = RESAMPLES, seed: int = 20261118) -> tuple[float, float, float]:
    """(mean, low, high) of the values, by resampling them with replacement (percentile interval)."""
    xs = list(values)
    if not xs:
        return float("nan"), float("nan"), float("nan")
    rng = random.Random(seed)
    n = len(xs)
    means = sorted(sum(xs[rng.randrange(n)] for _ in range(n)) / n for _ in range(resamples))
    lo = means[int((1 - level) / 2 * resamples)]
    hi = means[min(resamples - 1, int((1 + level) / 2 * resamples))]
    return mean(xs), lo, hi


def paired(a: dict, b: dict, resamples: int = RESAMPLES, seed: int = 20261118) -> dict:
    """a, b: {question id: per-question score}. The questions both have; mean difference a - b, its 95% interval, and
    a two-sided p-value (bootstrap of the paired differences, centred: how often a difference at least this large
    arises when there is none)."""
    common = sorted(set(a) & set(b))
    d = [a[q] - b[q] for q in common]
    if not d:
        return {"n": 0}
    m, lo, hi = bootstrap_ci(d, resamples=resamples, seed=seed)
    rng = random.Random(seed + 1)
    centred = [x - m for x in d]
    n = len(d)
    extreme = sum(1 for _ in range(resamples) if abs(sum(centred[rng.randrange(n)] for _ in range(n)) / n) >= abs(m) - 1e-12)
    return {"n": n, "diff": m, "low": lo, "high": hi, "p": (extreme + 1) / (resamples + 1)}


def wilson(k: int, n: int, z: float = 1.959964) -> tuple[float, float, float]:
    """(rate, low, high): the Wilson score interval of k successes in n trials. Unlike the bootstrap it does not collapse
    to [0, 0] when nothing happened: 0 of 180 still allows up to about 2% (what a rate of zero events can claim)."""
    if n == 0:
        return float("nan"), float("nan"), float("nan")
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return p, max(0.0, c - h), min(1.0, c + h)


def cohen_kappa(pairs) -> float:
    """Agreement of two raters on categorical labels: [(label_1, label_2), ...]."""
    pairs = list(pairs)
    if not pairs:
        return float("nan")
    labels = sorted({x for p in pairs for x in p})
    n = len(pairs)
    po = sum(1 for x, y in pairs if x == y) / n
    pe = sum((sum(1 for x, _ in pairs if x == lab) / n) * (sum(1 for _, y in pairs if y == lab) / n) for lab in labels)
    return 1.0 if pe == 1 else (po - pe) / (1 - pe)


def percentile(xs, p: float) -> float:
    xs = sorted(xs)
    if not xs:
        return float("nan")
    k = (len(xs) - 1) * p
    f, c = math.floor(k), math.ceil(k)
    return xs[f] if f == c else xs[f] + (xs[c] - xs[f]) * (k - f)
