import json

import pytest

from Geomag.progress_matching import (
    OnlineMagneticProgressMatcher,
    load_progress_matcher,
)


def test_online_progress_match_is_causal_and_monotonic():
    matcher = OnlineMagneticProgressMatcher(
        [[0, 0, 0], [1, 0, 0], [2, 1, 0], [3, 1, 1]],
        [0.1, 0.3, 0.6, 0.9],
        transition_penalty=0.5,
    )
    matches = [
        matcher.update(vector)
        for vector in [[10, 5, 1], [11, 5, 1], [12, 6, 1], [13, 6, 2]]
    ]

    assert [item.progress for item in matches] == sorted(
        item.progress for item in matches
    )
    assert matches[-1].template_index >= 2
    assert matches[-1].observation_count == 4


def test_load_progress_matcher_drops_origin_progress(tmp_path):
    path = tmp_path / "template.json"
    path.write_text(
        json.dumps(
            {
                "geomagnetic_vector_history": [
                    [0, 0, 0],
                    [1, 0, 0],
                    [2, 0, 0],
                ],
                "track_progress": [0.0, 0.1, 0.5, 0.9],
            }
        ),
        encoding="utf-8",
    )

    matcher = load_progress_matcher(path)

    assert matcher.template_progress.tolist() == pytest.approx([0.1, 0.5, 0.9])


def test_progress_matcher_rejects_non_monotonic_template():
    with pytest.raises(ValueError, match="monotonic"):
        OnlineMagneticProgressMatcher(
            [[0, 0, 0], [1, 0, 0], [2, 0, 0]],
            [0.0, 0.8, 0.4],
        )
