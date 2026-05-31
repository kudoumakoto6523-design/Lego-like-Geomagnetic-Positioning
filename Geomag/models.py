import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt

from Geomag.distance import latlon_to_xy


@dataclass
class RunContext:
    num_runs: int
    window_size: int
    geomag_map: Any
    route_source: str = "uji"
    sensor_source: str = "uji"
    data_root: str = "data/raw"
    uji_test_file: str = "tt01.txt"
    own_data_dir: str = "data/Geomagnetic Navigation 2026-03-03 15-28-45"
    own_dataset_key: str | None = None


@dataclass
class Particle:
    x: float = 0.0
    y: float = 0.0
    theta: float = 0.0
    weight: float = 1.0
    mag_hist: list[float] = field(default_factory=list)
    alive: bool = True


class PFState:
    """Particle-filter state: particles, map, resampling, and estimation."""

    def __init__(
        self,
        init_pos: Any,
        mag_map: Any,
        num_particles: int = 50000,
        seed: int = 42,
        weight_sigma: float = 8.0,
        map_knn_k: int = 10,
        map_idw_power: float = 2.0,
        min_particles: int = 1000,
        max_particles: int = 100000000,
    ) -> None:
        self.mag_map = mag_map
        self.rng = np.random.default_rng(seed)
        self.weight_sigma = float(weight_sigma)
        self.map_knn_k = max(1, int(map_knn_k))
        self.map_idw_power = float(map_idw_power)
        self.min_particles = int(min_particles)
        self.max_particles = int(max_particles)
        self.n_particles = int(np.clip(num_particles, self.min_particles, self.max_particles))
        self.map_points = self._load_map_points(mag_map)
        self.strict_map_bounds = self._infer_strict_map_bounds(mag_map, self.map_points)
        self.map_bounds = self._infer_map_bounds(self.map_points)
        self.x0, self.y0 = self._normalize_init_pos(init_pos, mag_map)
        self.particles = self._spawn_particles(self.n_particles)
        self._normalize_weights()
        self.estimate = self._estimate_xy()

    def _normalize_init_pos(self, init_pos, mag_map) -> tuple[float, float]:
        arr = np.asarray(init_pos, dtype=float).reshape(-1)
        if arr.size < 2:
            return 0.0, 0.0
        a, b = float(arr[0]), float(arr[1])

        # If map provides geo origin and pos looks like lat/lon, convert to local xy.
        try:
            if isinstance(mag_map, dict) and "output_model_npz" in mag_map:
                p = Path(mag_map["output_model_npz"])
                if p.exists():
                    model = np.load(p)
                    if "origin_lat" in model and "origin_lon" in model and abs(a) <= 90 and abs(b) <= 180:
                        lat0 = float(model["origin_lat"][0])
                        lon0 = float(model["origin_lon"][0])
                        return latlon_to_xy(a, b, lat0, lon0)
        except Exception:
            pass

        return a, b

    def _load_map_points(self, mag_map) -> dict[str, npt.NDArray[np.float64]] | None:
        if not isinstance(mag_map, dict):
            return None

        # UJI continuous model points
        if "output_model_npz" in mag_map:
            path = Path(mag_map["output_model_npz"])
            if path.exists():
                model = np.load(path)
                x = np.asarray(model.get("x_train", []), dtype=float)
                y = np.asarray(model.get("y_train", []), dtype=float)
                z = np.asarray(model.get("z_train", []), dtype=float)
                if x.size and y.size and z.size and x.size == y.size == z.size:
                    return {"x": x, "y": y, "z": z}

        # Own grid points
        if mag_map.get("source") == "own":
            cloud = np.asarray(mag_map.get("map_points", []), dtype=float)
            if cloud.ndim == 2 and cloud.shape[1] >= 3 and cloud.shape[0] > 0:
                finite = np.isfinite(cloud[:, 0]) & np.isfinite(cloud[:, 1]) & np.isfinite(cloud[:, 2])
                if np.any(finite):
                    return {"x": cloud[finite, 0], "y": cloud[finite, 1], "z": cloud[finite, 2]}

            grid_array = mag_map.get("grid_array")
            if grid_array is not None:
                grid = np.asarray(grid_array, dtype=float)
                if grid.ndim == 2 and grid.size > 0:
                    meta = mag_map.get("grid_map_contract", {}).get("meta", {})
                    origin = meta.get("origin_xy_m", [0.0, 0.0])
                    ox = float(origin[0]) if len(origin) > 0 else 0.0
                    oy = float(origin[1]) if len(origin) > 1 else 0.0
                    tile_size_x_m = float(meta.get("tile_size_x_m", meta.get("cell_size_m", 1.0)) or 1.0)
                    tile_size_y_m = float(meta.get("tile_size_y_m", meta.get("cell_size_m", 1.0)) or 1.0)
                    anchor = str(meta.get("anchor", "center")).strip().lower()
                    if anchor not in {"center", "corner"}:
                        anchor = "center"

                    flip_raw = meta.get("flip_y", True)
                    if isinstance(flip_raw, str):
                        flip_y = flip_raw.strip().lower() in {"1", "true", "yes", "y", "on"}
                    else:
                        flip_y = bool(flip_raw)

                    rows, cols = grid.shape
                    col_idx = np.tile(np.arange(cols, dtype=float), rows)
                    row_idx = np.repeat(np.arange(rows, dtype=float), cols)
                    y_idx = (rows - 1.0 - row_idx) if flip_y else row_idx
                    offset_x = 0.5 * tile_size_x_m if anchor == "center" else 0.0
                    offset_y = 0.5 * tile_size_y_m if anchor == "center" else 0.0

                    z = grid.reshape(-1)
                    finite = np.isfinite(z)
                    if np.any(finite):
                        x = ox + col_idx * tile_size_x_m + offset_x
                        y = oy + y_idx * tile_size_y_m + offset_y
                        return {"x": x[finite], "y": y[finite], "z": z[finite]}

        return None

    def _spawn_particles(self, n: int, center=None) -> list[Particle]:
        if center is None:
            cx, cy = self.x0, self.y0
        else:
            cx, cy = float(center[0]), float(center[1])
        particles = []
        attempts = 0
        max_attempts = max(100, int(n) * 20)
        while len(particles) < int(n) and attempts < max_attempts:
            attempts += 1
            px = float(cx + self.rng.normal(0.0, 0.8))
            py = float(cy + self.rng.normal(0.0, 0.8))
            if not self.in_strict_map_bounds(px, py):
                continue
            particles.append(
                Particle(
                    x=px,
                    y=py,
                    theta=float(self.rng.uniform(-math.pi, math.pi)),
                    weight=1.0 / max(n, 1),
                )
            )
        while len(particles) < int(n):
            px, py = self._random_in_strict_map()
            particles.append(
                Particle(
                    x=px,
                    y=py,
                    theta=float(self.rng.uniform(-math.pi, math.pi)),
                    weight=1.0 / max(n, 1),
                )
            )
        return particles

    @staticmethod
    def _infer_strict_map_bounds(mag_map, map_points):
        if isinstance(mag_map, dict):
            keys = ("rangex_min", "rangex_max", "rangey_min", "rangey_max")
            if all(k in mag_map for k in keys):
                try:
                    return (
                        float(mag_map["rangex_min"]),
                        float(mag_map["rangex_max"]),
                        float(mag_map["rangey_min"]),
                        float(mag_map["rangey_max"]),
                    )
                except (TypeError, ValueError):
                    pass
        return PFState._infer_map_bounds(map_points, pad=0.0)

    @staticmethod
    def _infer_map_bounds(map_points, pad=0.4):
        if map_points is None:
            return None
        x = np.asarray(map_points.get("x", []), dtype=float)
        y = np.asarray(map_points.get("y", []), dtype=float)
        if x.size == 0 or y.size == 0:
            return None
        return (
            float(np.min(x) - pad),
            float(np.max(x) + pad),
            float(np.min(y) - pad),
            float(np.max(y) + pad),
        )

    def clamp_to_map(self, x: float, y: float) -> tuple[float, float]:
        if self.map_bounds is None:
            return float(x), float(y)
        min_x, max_x, min_y, max_y = self.map_bounds
        return float(np.clip(x, min_x, max_x)), float(np.clip(y, min_y, max_y))

    def clamp_to_strict_map(self, x: float, y: float) -> tuple[float, float]:
        if self.strict_map_bounds is None:
            return float(x), float(y)
        min_x, max_x, min_y, max_y = self.strict_map_bounds
        return float(np.clip(x, min_x, max_x)), float(np.clip(y, min_y, max_y))

    def in_strict_map_bounds(self, x: float, y: float) -> bool:
        if self.strict_map_bounds is None:
            return True
        min_x, max_x, min_y, max_y = self.strict_map_bounds
        return bool(min_x <= float(x) <= max_x and min_y <= float(y) <= max_y)

    def _random_in_strict_map(self) -> tuple[float, float]:
        if self.strict_map_bounds is None:
            return self.x0, self.y0
        min_x, max_x, min_y, max_y = self.strict_map_bounds
        return (
            float(self.rng.uniform(min_x, max_x)),
            float(self.rng.uniform(min_y, max_y)),
        )

    @staticmethod
    def kill_particle(p: Particle) -> None:
        p.alive = False
        p.weight = 0.0

    def _normalize_weights(self) -> None:
        live = [p for p in self.particles if getattr(p, "alive", True)]
        total = float(sum(max(p.weight, 0.0) for p in live))
        if total <= 1e-12:
            # Soft recovery: inject tiny weights before hard respawn so
            # particles that were killed by a single bad match can recover.
            n_live = len(live)
            n_total = len(self.particles)
            if n_total > 0 and n_live == 0:
                # All dead — give every particle a uniform tiny weight
                # and re-check on next cycle.
                tiny = 1.0 / float(n_total)
                for p in self.particles:
                    p.alive = True
                    p.weight = tiny
                total = 1.0
            else:
                n = int(np.clip(n_total or self.min_particles, self.min_particles, self.max_particles))
                center = getattr(self, "estimate", (self.x0, self.y0))
                self.particles = self._spawn_particles(n, center=center)
                w = 1.0 / max(len(self.particles), 1)
                for p in self.particles:
                    p.alive = True
                    p.weight = w
                return
        for p in self.particles:
            if getattr(p, "alive", True):
                p.weight = max(p.weight, 0.0) / total
            else:
                p.weight = 0.0

    def _estimate_xy(self) -> tuple[float, float]:
        if not self.particles:
            return (0.0, 0.0)
        self._normalize_weights()
        live = [p for p in self.particles if getattr(p, "alive", True)]
        xs = np.asarray([p.x for p in live], dtype=float)
        ys = np.asarray([p.y for p in live], dtype=float)
        ws = np.asarray([p.weight for p in live], dtype=float)
        return float(np.sum(xs * ws)), float(np.sum(ys * ws))

    def get_pos(self) -> tuple[float, float]:
        self.estimate = self._estimate_xy()
        return self.estimate

    def effective_sample_size(self) -> float:
        if not self.particles:
            return 0.0
        ws = np.asarray([p.weight for p in self.particles], dtype=float)
        denom = float(np.sum(ws * ws))
        if denom <= 1e-12:
            return 0.0
        return float(1.0 / denom)

    def map_magnitude(self, x: float, y: float, k: int | None = None) -> float:
        if self.map_points is None:
            return 0.0
        if not self.in_strict_map_bounds(x, y):
            return float("nan")
        px = self.map_points["x"]
        py = self.map_points["y"]
        pz = self.map_points["z"]
        if px.size == 0:
            return 0.0
        dx = px - float(x)
        dy = py - float(y)
        dist2 = dx * dx + dy * dy
        kk_raw = self.map_knn_k if k is None else int(k)
        kk = max(1, min(int(kk_raw), dist2.size))
        idx = np.argpartition(dist2, kk - 1)[:kk]
        d = np.sqrt(dist2[idx]) + 1e-6
        w = 1.0 / (d ** self.map_idw_power)
        return float(np.sum(w * pz[idx]) / np.sum(w))

    def adapt_particle_count_kld(self, epsilon: float = 0.12, z: float = 1.96, bin_size_xy: float = 0.8, bin_size_theta: float = 0.35) -> int:
        if not self.particles:
            return self.min_particles
        bins = set()
        for p in self.particles:
            if not getattr(p, "alive", True):
                continue
            bx = int(math.floor(p.x / float(bin_size_xy)))
            by = int(math.floor(p.y / float(bin_size_xy)))
            bt = int(math.floor((p.theta + math.pi) / float(bin_size_theta)))
            bins.add((bx, by, bt))
        k = len(bins)
        if k <= 1:
            return self.min_particles
        n = (k - 1) / (2.0 * float(epsilon))
        t = 1.0 - 2.0 / (9.0 * (k - 1)) + float(z) * math.sqrt(2.0 / (9.0 * (k - 1)))
        n = int(math.ceil(n * (t ** 3)))
        return int(np.clip(n, self.min_particles, self.max_particles))

    def systematic_resample(self, target_count: int | None = None,
                            inject_ratio: float = 0.05, noise_scale: float = 0.15) -> None:
        """Systematic resampling with diversity injection.

        Preserves high-weight particles through stratified sampling, then
        injects a small fraction of random particles to maintain diversity.
        """
        if not self.particles:
            self.particles = self._spawn_particles(self.min_particles)
            return
        self._normalize_weights()
        live = [p for p in self.particles if getattr(p, "alive", True) and p.weight > 0.0]
        if not live:
            center = getattr(self, "estimate", (self.x0, self.y0))
            self.particles = self._spawn_particles(self.min_particles, center=center)
            self._normalize_weights()
            return

        n_live = len(live)
        if target_count is None:
            target_count = n_live
        target_count = int(np.clip(target_count, self.min_particles, self.max_particles))
        n_inject = max(1, int(target_count * inject_ratio))
        n_copy = target_count - n_inject

        weights = np.asarray([p.weight for p in live], dtype=float)
        weights /= weights.sum()
        cumsum = np.cumsum(weights)

        new_particles = []
        u0 = float(self.rng.uniform(0.0, 1.0 / n_copy))
        for i in range(n_copy):
            u = u0 + float(i) / n_copy
            idx = int(np.searchsorted(cumsum, u))
            idx = min(idx, n_live - 1)
            base = live[idx]
            new_particles.append(Particle(
                x=float(base.x + self.rng.normal(0.0, noise_scale)),
                y=float(base.y + self.rng.normal(0.0, noise_scale)),
                theta=float(((base.theta + self.rng.normal(0.0, 0.08) + math.pi)
                             % (2.0 * math.pi)) - math.pi),
                weight=1.0 / target_count,
                mag_hist=list(base.mag_hist[-64:]),
            ))

        for _ in range(n_inject):
            nx, ny = self._random_in_strict_map()
            new_particles.append(Particle(
                x=nx, y=ny,
                theta=float(self.rng.uniform(-math.pi, math.pi)),
                weight=1.0 / target_count,
            ))

        self.particles = new_particles
        self.n_particles = len(self.particles)
        self._normalize_weights()

    def cso_resample(self, target_count: int | None = None) -> None:
        if not self.particles:
            self.particles = self._spawn_particles(self.min_particles)
            return
        self._normalize_weights()
        particles = sorted(
            [p for p in self.particles if getattr(p, "alive", True) and p.weight > 0.0],
            key=lambda p: p.weight,
            reverse=True,
        )
        if not particles:
            center = getattr(self, "estimate", (self.x0, self.y0))
            self.particles = self._spawn_particles(self.min_particles, center=center)
            self.n_particles = len(self.particles)
            self._normalize_weights()
            return
        n = len(particles)
        if target_count is None:
            target_count = n
        target_count = int(np.clip(target_count, self.min_particles, self.max_particles))

        n_rooster = max(1, int(0.2 * n))
        n_hen = max(1, int(0.5 * n))
        roosters = particles[:n_rooster]
        hens = particles[n_rooster : n_rooster + n_hen]
        chicks = particles[n_rooster + n_hen :]

        gbest = roosters[0]
        new_particles = []
        attempts = 0
        max_attempts = max(100, target_count * 20)
        while len(new_particles) < target_count and attempts < max_attempts:
            attempts += 1
            role_r = float(self.rng.random())
            if role_r < 0.25 and roosters:
                base = roosters[int(self.rng.integers(0, len(roosters)))]
                history_seed = list(base.mag_hist)
                nx = base.x + float(self.rng.normal(0.0, 0.35))
                ny = base.y + float(self.rng.normal(0.0, 0.35))
                nt = base.theta + float(self.rng.normal(0.0, 0.08))
            elif role_r < 0.75 and hens and roosters:
                h = hens[int(self.rng.integers(0, len(hens)))]
                r = roosters[int(self.rng.integers(0, len(roosters)))]
                history_seed = list(h.mag_hist)
                nx = h.x + 0.6 * (r.x - h.x) + 0.2 * (gbest.x - h.x) + float(self.rng.normal(0.0, 0.25))
                ny = h.y + 0.6 * (r.y - h.y) + 0.2 * (gbest.y - h.y) + float(self.rng.normal(0.0, 0.25))
                nt = h.theta + 0.3 * (r.theta - h.theta) + float(self.rng.normal(0.0, 0.06))
            else:
                leader = hens[int(self.rng.integers(0, len(hens)))] if hens else gbest
                c = chicks[int(self.rng.integers(0, len(chicks)))] if chicks else leader
                history_seed = list(c.mag_hist)
                nx = c.x + 0.8 * (leader.x - c.x) + float(self.rng.normal(0.0, 0.3))
                ny = c.y + 0.8 * (leader.y - c.y) + float(self.rng.normal(0.0, 0.3))
                nt = c.theta + 0.6 * (leader.theta - c.theta) + float(self.rng.normal(0.0, 0.08))

            if not self.in_strict_map_bounds(nx, ny):
                continue

            new_particles.append(
                Particle(
                    x=float(nx),
                    y=float(ny),
                    theta=float(((nt + math.pi) % (2.0 * math.pi)) - math.pi),
                    weight=1.0 / target_count,
                    mag_hist=history_seed[-64:],
                )
            )
        while len(new_particles) < target_count:
            nx, ny = self._random_in_strict_map()
            new_particles.append(
                Particle(
                    x=float(nx),
                    y=float(ny),
                    theta=float(self.rng.uniform(-math.pi, math.pi)),
                    weight=1.0 / target_count,
                )
            )
        self.particles = new_particles
        self.n_particles = len(self.particles)
        self._normalize_weights()


# Backward-compatible alias to keep naming close to prior script.
PF_State = PFState
