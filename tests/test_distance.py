"""Tests for Geomag.distance — shared math utilities."""

import math

import numpy as np
import pytest

from Geomag.distance import (
    derivative_sequence,
    zscore,
    ddtw_distance,
    wrap_angle_pi,
    latlon_to_xy,
)


class TestDerivativeSequence:
    def test_empty(self):
        result = derivative_sequence([])
        assert result.size == 0

    def test_single(self):
        result = derivative_sequence([5.0])
        assert result.size == 1
        assert result[0] == 5.0

    def test_two_elements(self):
        result = derivative_sequence([1.0, 3.0])
        assert result.size == 1
        assert result[0] == 2.0

    def test_constant(self):
        result = derivative_sequence([1.0, 1.0, 1.0, 1.0])
        assert result.size == 4
        np.testing.assert_array_almost_equal(result, np.zeros(4))

    def test_linear(self):
        result = derivative_sequence([1.0, 2.0, 3.0, 4.0])
        assert result.size == 4
        np.testing.assert_array_almost_equal(result, np.ones(4))


class TestZScore:
    def test_empty(self):
        result = zscore([])
        assert result.size == 0

    def test_single(self):
        result = zscore([5.0])
        assert result[0] == 0.0

    def test_normalization(self):
        result = zscore([1.0, 2.0, 3.0, 4.0, 5.0])
        assert abs(float(np.mean(result))) < 1e-10
        assert abs(float(np.std(result)) - 1.0) < 1e-10

    def test_near_zero_variance(self):
        result = zscore([1.0, 1.0, 1.0])
        assert result.size == 3
        assert float(np.std(result)) < 1e-8


class TestDDTWDistance:
    def test_identical_sequences(self):
        a = [1.0, 2.0, 3.0, 4.0, 5.0]
        dist = ddtw_distance(a, a)
        assert dist == pytest.approx(0.0, abs=1e-10)

    def test_different_sequences(self):
        a = [1.0, 2.0, 3.0, 4.0, 5.0]
        b = [1.0, 2.0, 3.0, 4.0, 10.0]
        dist = ddtw_distance(a, b)
        assert dist > 0.0

    def test_empty_input(self):
        dist = ddtw_distance([], [1.0, 2.0])
        assert dist == 0.0

    def test_short_input(self):
        dist = ddtw_distance([1.0], [2.0])
        assert isinstance(dist, float)
        assert dist >= 0.0

    def test_window_ratio_effect(self):
        a = [1.0, 2.0, 1.0, 2.0, 1.0]
        b = [2.0, 1.0, 2.0, 1.0, 2.0]
        d1 = ddtw_distance(a, b, window_ratio=0.1)
        d2 = ddtw_distance(a, b, window_ratio=1.0)
        # Wider window should give <= distance (more warping freedom)
        assert d2 <= d1 or d1 == pytest.approx(d2, rel=0.01)


class TestWrapAnglePi:
    def test_zero(self):
        assert wrap_angle_pi(0.0) == 0.0

    def test_pi(self):
        # Both π and -π are valid; the function returns one of them.
        result = wrap_angle_pi(math.pi)
        assert abs(abs(result) - math.pi) < 1e-10

    def test_negative_pi(self):
        result = wrap_angle_pi(-math.pi)
        assert abs(abs(result) - math.pi) < 1e-10

    def test_wrap(self):
        assert wrap_angle_pi(3.5) == pytest.approx(3.5 - 2 * math.pi)
        assert wrap_angle_pi(-3.5) == pytest.approx(-3.5 + 2 * math.pi)

    def test_large_angle(self):
        assert wrap_angle_pi(10 * math.pi) == pytest.approx(0.0, abs=1e-10)


class TestLatLonToXY:
    def test_zero_displacement(self):
        x, y = latlon_to_xy(39.9, 116.4, 39.9, 116.4)
        assert float(x) == pytest.approx(0.0, abs=1e-6)
        assert float(y) == pytest.approx(0.0, abs=1e-6)

    def test_north_displacement(self):
        x, y = latlon_to_xy(40.0, 116.4, 39.9, 116.4)
        assert float(y) > 0  # going north = positive y
        assert abs(float(x)) < 1.0  # almost no east-west change

    def test_east_displacement(self):
        x, y = latlon_to_xy(39.9, 116.5, 39.9, 116.4)
        assert float(x) > 0  # going east = positive x

    def test_array_input(self):
        import numpy as np
        lats = np.array([39.9, 39.91])
        lons = np.array([116.4, 116.41])
        x, y = latlon_to_xy(lats, lons, 39.9, 116.4)
        assert x.shape == (2,)
        assert y.shape == (2,)
        assert float(x[0]) == pytest.approx(0.0, abs=1e-6)
