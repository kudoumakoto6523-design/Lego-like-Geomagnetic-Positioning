"""Tests for Geomag.models — Particle, PFState, RunContext."""

import math

import numpy as np
import pytest

from Geomag.models import Particle, PFState, RunContext


class TestParticle:
    def test_default_creation(self):
        p = Particle()
        assert p.x == 0.0
        assert p.y == 0.0
        assert p.alive is True
        assert p.weight == 1.0
        assert p.mag_hist == []

    def test_custom_creation(self):
        p = Particle(x=1.5, y=2.5, theta=1.0, weight=0.5)
        assert p.x == 1.5
        assert p.y == 2.5
        assert p.theta == 1.0
        assert p.weight == 0.5

    def test_mag_hist(self):
        p = Particle(mag_hist=[45.0, 46.0])
        assert p.mag_hist == [45.0, 46.0]


class TestRunContext:
    def test_default_creation(self):
        ctx = RunContext(num_runs=3, window_size=400, geomag_map={"source": "uji"})
        assert ctx.num_runs == 3
        assert ctx.window_size == 400
        assert ctx.route_source == "uji"
        assert ctx.sensor_source == "uji"


class TestPFStateInit:
    def test_basic_creation(self, simple_mag_map):
        state = PFState(
            init_pos=[1.0, 1.0],
            mag_map=simple_mag_map,
            num_particles=1200,
            min_particles=50,
            seed=42,
        )
        assert len(state.particles) == 1200
        assert state.x0 == 1.0
        assert state.y0 == 1.0

    def test_particle_count_clamped(self, simple_mag_map):
        state = PFState(
            init_pos=[0, 0],
            mag_map=simple_mag_map,
            num_particles=10,
            min_particles=100,
            max_particles=500,
        )
        assert len(state.particles) == 100  # clamped up to min

    def test_particles_within_bounds(self, simple_mag_map):
        state = PFState(
            init_pos=[1.0, 1.0],
            mag_map=simple_mag_map,
            num_particles=200,
            seed=42,
        )
        for p in state.particles:
            assert state.in_strict_map_bounds(p.x, p.y), f"particle ({p.x}, {p.y}) out of bounds"

    def test_estimate_initialized(self, simple_mag_map):
        state = PFState(init_pos=[1.0, 1.0], mag_map=simple_mag_map, num_particles=100)
        ex, ey = state.get_pos()
        assert math.isfinite(ex)
        assert math.isfinite(ey)
        assert 0.0 <= ex <= 2.0
        assert 0.0 <= ey <= 2.0


class TestPFStateNormalization:
    def test_weights_sum_to_one(self, pf_state):
        pf_state._normalize_weights()
        total = sum(p.weight for p in pf_state.particles if p.alive)
        assert total == pytest.approx(1.0, abs=1e-6)

    def test_dead_particle_zero_weight(self, pf_state):
        pf_state.particles[0].weight = 0.5
        pf_state.kill_particle(pf_state.particles[0])
        assert pf_state.particles[0].alive is False
        assert pf_state.particles[0].weight == 0.0

    def test_normalization_respawns_on_zero_weights(self, pf_state):
        for p in pf_state.particles:
            p.weight = 0.0
        pf_state._normalize_weights()
        # Should have respawned particles
        assert len(pf_state.particles) >= pf_state.min_particles
        total = sum(p.weight for p in pf_state.particles)
        assert total == pytest.approx(1.0, abs=1e-6)


class TestPFStateBounds:
    def test_strict_bounds(self, simple_mag_map):
        state = PFState(init_pos=[1, 1], mag_map=simple_mag_map, num_particles=10)
        assert state.in_strict_map_bounds(1.0, 1.0) is True
        assert state.in_strict_map_bounds(100.0, 100.0) is False

    def test_clamp_to_map(self, simple_mag_map):
        state = PFState(init_pos=[1, 1], mag_map=simple_mag_map, num_particles=10)
        x, y = state.clamp_to_map(100.0, -50.0)
        # With pad=0.4: map bounds are [0, 2] → padded to [-0.4, 2.4]
        assert 0.0 - 0.4 <= x <= 2.0 + 0.4
        assert 0.0 - 0.4 <= y <= 2.0 + 0.4

    def test_clamp_to_strict_map(self, simple_mag_map):
        state = PFState(init_pos=[1, 1], mag_map=simple_mag_map, num_particles=10)
        x, y = state.clamp_to_strict_map(100.0, -50.0)
        assert 0.0 <= x <= 2.0
        assert 0.0 <= y <= 2.0


class TestPFStateMapMagnitude:
    def test_returns_float(self, pf_state):
        mag = pf_state.map_magnitude(1.0, 1.0)
        assert isinstance(mag, float)
        assert math.isfinite(mag)

    def test_out_of_bounds_returns_nan(self, pf_state):
        mag = pf_state.map_magnitude(100.0, 100.0)
        assert not math.isfinite(mag)

    def test_near_point_interpolation(self, pf_state):
        # At a known map point, should return close to the stored value
        mag = pf_state.map_magnitude(0.0, 0.0)
        assert 40.0 <= mag <= 50.0  # map point at (0,0) has z=45.0


class TestPFStateEffectiveSampleSize:
    def test_uniform_weights(self, pf_state):
        for p in pf_state.particles:
            if p.alive:
                p.weight = 1.0 / len(pf_state.particles)
        pf_state._normalize_weights()
        ess = pf_state.effective_sample_size()
        n = len([p for p in pf_state.particles if p.alive])
        assert ess == pytest.approx(n, rel=0.01)

    def test_degenerate_weights(self, pf_state):
        for p in pf_state.particles:
            p.weight = 0.0
        pf_state.particles[0].weight = 1.0
        ess = pf_state.effective_sample_size()
        assert ess == pytest.approx(1.0, abs=0.1)


class TestPFStateKLD:
    def test_returns_int(self, pf_state):
        n = pf_state.adapt_particle_count_kld()
        assert isinstance(n, int)
        assert pf_state.min_particles <= n <= pf_state.max_particles

    def test_respects_bounds(self, pf_state):
        n = pf_state.adapt_particle_count_kld(epsilon=0.5)
        assert pf_state.min_particles <= n <= pf_state.max_particles


class TestPFStateCSOResample:
    def test_maintains_particle_count(self, pf_state):
        original_n = len(pf_state.particles)
        pf_state.cso_resample(target_count=original_n)
        assert len(pf_state.particles) == original_n

    def test_respects_target_count(self, pf_state):
        # Note: min_particles default (1000) may clamp target_count up
        target = max(60, pf_state.min_particles)
        pf_state.cso_resample(target_count=target)
        assert len(pf_state.particles) == target

    def test_weights_normalized_after_resample(self, pf_state):
        pf_state.cso_resample(target_count=80)
        total = sum(p.weight for p in pf_state.particles if p.alive)
        assert total == pytest.approx(1.0, abs=1e-6)

    def test_particles_in_bounds_after_resample(self, pf_state):
        pf_state.cso_resample(target_count=80)
        for p in pf_state.particles:
            assert pf_state.in_strict_map_bounds(p.x, p.y), \
                f"resampled particle ({p.x:.2f}, {p.y:.2f}) out of bounds"


class TestPFStateSpawn:
    def test_spawn_respects_count(self, pf_state):
        particles = pf_state._spawn_particles(25)
        assert len(particles) == 25
        assert all(isinstance(p, Particle) for p in particles)

    def test_spawned_particles_in_bounds(self, pf_state):
        particles = pf_state._spawn_particles(100)
        for p in particles:
            assert pf_state.in_strict_map_bounds(p.x, p.y)

    def test_spawn_around_center(self, pf_state):
        particles = pf_state._spawn_particles(200, center=[0.5, 0.5])
        xs = [p.x for p in particles]
        ys = [p.y for p in particles]
        mean_x = float(np.mean(xs))
        mean_y = float(np.mean(ys))
        # Center of spawn should be near (0.5, 0.5)
        assert abs(mean_x - 0.5) < 1.0
        assert abs(mean_y - 0.5) < 1.0
