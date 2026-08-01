"""Online progress matching against an independently captured route template."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np


@dataclass
class ProgressMatch:
    progress: float
    template_index: int
    observation_count: int
    normalized_cost: float

    def as_dict(self):
        return {
            "progress": float(self.progress),
            "template_index": int(self.template_index),
            "observation_count": int(self.observation_count),
            "normalized_cost": float(self.normalized_cost),
        }


class OnlineMagneticProgressMatcher:
    """Causal Viterbi/DTW matcher using step-to-step magnetic-vector changes.

    The state is constrained to move forward through the template by at most
    ``max_advance`` samples per observed step.  Only past observations are
    used, so the result can be consumed online.
    """

    def __init__(
        self,
        template_vectors,
        template_progress,
        *,
        max_advance=3,
        transition_penalty=0.5,
        expected_advance=1.0,
    ):
        vectors = np.asarray(template_vectors, dtype=float)
        progress = np.asarray(template_progress, dtype=float).reshape(-1)
        if vectors.ndim != 2 or vectors.shape[0] < 3 or vectors.shape[1] != 3:
            raise ValueError("template_vectors must have shape (N, 3), N >= 3.")
        if progress.size != vectors.shape[0]:
            raise ValueError("template_progress must match template_vectors length.")
        if np.any(np.diff(progress) < 0.0):
            raise ValueError("template_progress must be monotonic.")

        deltas = np.diff(vectors, axis=0, prepend=vectors[:1])
        scale = np.maximum(np.std(deltas[1:], axis=0), 0.5)
        self.template_deltas = deltas / scale
        self.delta_scale = scale
        self.template_progress = np.clip(progress, 0.0, 1.0)
        self.max_advance = max(1, int(max_advance))
        self.transition_penalty = max(0.0, float(transition_penalty))
        self.expected_advance = max(0.0, float(expected_advance))
        self.costs = np.full(vectors.shape[0], np.inf, dtype=float)
        self.costs[0] = 0.0
        self.previous_vector = None
        self.observation_count = 0
        self.committed_index = 0

    def update(self, magnetic_vector):
        vector = np.asarray(magnetic_vector, dtype=float).reshape(-1)
        if vector.size != 3 or not np.all(np.isfinite(vector)):
            raise ValueError("magnetic_vector must contain three finite values.")
        self.observation_count += 1
        if self.previous_vector is None:
            self.previous_vector = vector
            return ProgressMatch(
                progress=float(self.template_progress[0]),
                template_index=0,
                observation_count=self.observation_count,
                normalized_cost=0.0,
            )

        observed_delta = (vector - self.previous_vector) / self.delta_scale
        self.previous_vector = vector
        emissions = np.mean(
            np.square(self.template_deltas - observed_delta[None, :]), axis=1
        )
        updated = np.full_like(self.costs, np.inf)
        for template_index in range(updated.size):
            best = np.inf
            for advance in range(self.max_advance + 1):
                previous_index = template_index - advance
                if previous_index < 0:
                    continue
                transition = self.transition_penalty * (
                    float(advance) - self.expected_advance
                ) ** 2
                best = min(best, self.costs[previous_index] + transition)
            updated[template_index] = best + emissions[template_index]

        minimum = float(np.min(updated))
        if np.isfinite(minimum):
            updated -= minimum
        self.costs = updated
        best_index = int(np.argmin(updated))
        self.committed_index = max(self.committed_index, best_index)
        committed_cost = float(updated[self.committed_index])
        return ProgressMatch(
            progress=float(self.template_progress[self.committed_index]),
            template_index=self.committed_index,
            observation_count=self.observation_count,
            normalized_cost=committed_cost,
        )


def load_progress_matcher(result_json, **kwargs):
    """Load an online matcher from a prior branch result JSON."""

    path = Path(result_json)
    payload = json.loads(path.read_text(encoding="utf-8"))
    vectors = payload.get("geomagnetic_vector_history")
    progress = payload.get("track_progress")
    if vectors is None or progress is None:
        raise ValueError(
            f"Progress template {path} lacks geomagnetic_vector_history or track_progress."
        )
    # track_progress includes the route origin; vector history contains steps only.
    if len(progress) == len(vectors) + 1:
        progress = progress[1:]
    return OnlineMagneticProgressMatcher(vectors, progress, **kwargs)
