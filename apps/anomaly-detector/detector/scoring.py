"""Anomaly scoring with an Isolation Forest.

Idea: describe each moment of a service by two numbers, (error rate, p95 latency).
The stable version's recent history forms a cloud of such points. An Isolation
Forest builds random trees that split the data until every point is alone; a
point that is separated after very few splits is unusual. We ask how quickly the
canary's current point gets isolated from the stable cloud.

Score (as defined in the Isolation Forest paper): around 0.5 or below means
"looks like the baseline", values towards 1.0 mean "clearly different".
"""

import numpy as np
from sklearn.ensemble import IsolationForest

CLOUD_SIZE = 128  # number of baseline points the forest is fitted on


def anomaly_score(
    baseline: np.ndarray, canary: np.ndarray, noise: np.ndarray, seed: int = 42
) -> float:
    """Score one canary observation against the stable baseline.

    baseline: shape (n, 2) rows of (error_rate, p95_seconds) from the stable version
    canary:   shape (2,) the canary's current (error_rate, p95_seconds)
    noise:    shape (2,) per-feature tolerance; smaller differences are ignored
    """
    rng = np.random.default_rng(seed)
    # 1. Resample the baseline to a fixed size, so the score means the same thing
    #    whether 10 or 100 samples are available. A healthy baseline is often
    #    perfectly flat (0 errors); jitter gives it a realistic width so tiny
    #    differences are not flagged as anomalies.
    rows = rng.integers(0, len(baseline), size=CLOUD_SIZE)
    cloud = baseline[rows] + rng.normal(0.0, noise, size=(CLOUD_SIZE, baseline.shape[1]))
    # 2. Only "worse than usual" matters: a faster or cleaner canary is fine.
    point = np.maximum(canary, np.median(baseline, axis=0))
    # 3. Fit on the baseline plus the canary point. Trees only split inside the
    #    range of their training data, so the point must be part of it to be
    #    isolated when it lies far outside the baseline.
    data = np.vstack([cloud, point])
    forest = IsolationForest(n_estimators=100, max_samples=len(data), random_state=seed)
    forest.fit(data)
    # sklearn returns the negated paper score; flip it back to 0..1.
    return float(-forest.score_samples(point.reshape(1, -1))[0])
