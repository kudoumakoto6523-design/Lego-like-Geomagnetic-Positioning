"""Shared distance, signal-processing, and coordinate utilities.

These functions are used by both the block implementations (``blocks.py``)
and the procedural API (``algorithms.py``) to avoid code duplication.
"""

import math

import numpy as np


# Earth radius in metres (WGS-84).
_EARTH_RADIUS_M = 6378137.0


def latlon_to_xy(lat, lon, lat0, lon0):
    """Convert (lat, lon) to local Cartesian (x, y) relative to an origin.

    Uses an equirectangular approximation valid for short baselines
    (indoor / campus scale).

    Parameters
    ----------
    lat, lon : float or array-like
        Target coordinates in degrees.
    lat0, lon0 : float
        Origin coordinates in degrees.

    Returns
    -------
    x : float or ndarray
        Easting in metres.
    y : float or ndarray
        Northing in metres.
    """
    dlat = np.radians(np.asarray(lat, dtype=float) - float(lat0))
    dlon = np.radians(np.asarray(lon, dtype=float) - float(lon0))
    x = _EARTH_RADIUS_M * dlon * np.cos(np.radians((np.asarray(lat, dtype=float) + float(lat0)) * 0.5))
    y = _EARTH_RADIUS_M * dlat
    return np.asarray(x, dtype=float), np.asarray(y, dtype=float)


def derivative_sequence(x):
    """Compute central-difference derivative of a 1-D sequence.

    Endpoints use one-sided differences.
    """
    x = np.asarray(x, dtype=float).reshape(-1)
    if x.size <= 1:
        return x.copy()
    if x.size == 2:
        return np.asarray([x[1] - x[0]], dtype=float)
    d = np.empty_like(x, dtype=float)
    d[0] = float(x[1] - x[0])
    d[-1] = float(x[-1] - x[-2])
    d[1:-1] = 0.5 * (x[2:] - x[:-2])
    return d


def zscore(x):
    """Z-score normalize a 1-D sequence (zero mean, unit variance).

    Returns the de-meaned signal when variance is near zero.
    """
    x = np.asarray(x, dtype=float).reshape(-1)
    if x.size == 0:
        return x
    mu = float(np.mean(x))
    sd = float(np.std(x))
    if sd < 1e-8:
        return x - mu
    return (x - mu) / sd


def ddtw_distance(a, b, window_ratio=0.25):
    """Derivative Dynamic Time Warping distance between two sequences.

    Parameters
    ----------
    a, b : array-like
        Input sequences.
    window_ratio : float
        Sakoe-Chiba band width as a fraction of the longer sequence length.

    Returns
    -------
    float
        Normalised DDTW distance.
    """
    a = zscore(derivative_sequence(a))
    b = zscore(derivative_sequence(b))
    if a.size == 0 or b.size == 0:
        return 0.0

    n, m = int(a.size), int(b.size)
    window = max(abs(n - m), int(max(n, m) * float(window_ratio)), 4)
    dp = np.full((n + 1, m + 1), np.inf, dtype=float)
    dp[0, 0] = 0.0
    for i in range(1, n + 1):
        j0 = max(1, i - window)
        j1 = min(m, i + window)
        ai = float(a[i - 1])
        for j in range(j0, j1 + 1):
            cost = abs(ai - float(b[j - 1]))
            dp[i, j] = cost + min(dp[i - 1, j], dp[i, j - 1], dp[i - 1, j - 1])
    return float(dp[n, m] / max(1, n + m))


def wrap_angle_pi(rad):
    """Wrap an angle (radians) to [-π, π]."""
    return float(((rad + math.pi) % (2.0 * math.pi)) - math.pi)


# Keep legacy private aliases for internal compatibility.
_derivative_sequence = derivative_sequence
_zscore = zscore
_ddtw_distance = ddtw_distance
_wrap_angle_pi = wrap_angle_pi
_latlon_to_xy = latlon_to_xy
