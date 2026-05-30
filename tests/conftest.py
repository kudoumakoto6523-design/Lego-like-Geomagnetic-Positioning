"""Shared fixtures for geomagnetic positioning tests."""

import numpy as np
import pytest

from Geomag.models import Particle, PFState


@pytest.fixture
def rng():
    """Deterministic random generator for reproducible tests."""
    return np.random.default_rng(42)


@pytest.fixture
def sample_particles():
    """A small set of particles for testing."""
    return [
        Particle(x=0.0, y=0.0, theta=0.0, weight=0.25),
        Particle(x=1.0, y=1.0, theta=1.57, weight=0.25),
        Particle(x=2.0, y=0.5, theta=-1.57, weight=0.25),
        Particle(x=0.5, y=2.0, theta=3.14, weight=0.25),
    ]


@pytest.fixture
def simple_map_points():
    """A small synthetic magnetic map for testing."""
    return {
        "x": np.array([0.0, 1.0, 2.0, 0.0, 1.0, 2.0, 0.0, 1.0, 2.0], dtype=float),
        "y": np.array([0.0, 0.0, 0.0, 1.0, 1.0, 1.0, 2.0, 2.0, 2.0], dtype=float),
        "z": np.array([45.0, 46.0, 44.0, 45.5, 45.0, 43.5, 44.0, 43.0, 42.0], dtype=float),
    }


@pytest.fixture
def simple_mag_map(simple_map_points):
    """A minimal geomag map dict for PFState construction."""
    return {
        "source": "own",
        "rangex_min": 0.0,
        "rangex_max": 2.0,
        "rangey_min": 0.0,
        "rangey_max": 2.0,
        "map_points": np.column_stack([
            simple_map_points["x"],
            simple_map_points["y"],
            simple_map_points["z"],
        ]),
    }


@pytest.fixture
def pf_state(simple_mag_map):
    """A PFState with a small synthetic map."""
    return PFState(
        init_pos=[1.0, 1.0],
        mag_map=simple_mag_map,
        num_particles=100,
        seed=42,
        weight_sigma=8.0,
    )


@pytest.fixture
def sensor_buffer():
    """A minimal sensor buffer simulating a few accelerometer/gyro/mag frames."""
    return [
        [[0.1, 0.2, 9.8], [0.01, 0.02, 0.03], [30.0, 40.0, 50.0]],
        [[0.2, 0.3, 9.7], [0.02, 0.01, 0.04], [31.0, 41.0, 51.0]],
        [[0.3, 0.4, 9.6], [0.03, 0.02, 0.05], [32.0, 42.0, 52.0]],
    ]
