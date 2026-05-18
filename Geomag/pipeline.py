import math
import sys
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from Geomag.algorithms import get_sensor, get_test_len, get_true_route, visualize
from Geomag.blocks import (
    HEADING_REGISTRY,
    MAG_REGISTRY,
    MOTION_REGISTRY,
    PARTICLE_SIZE_REGISTRY,
    RESAMPLE_TRIGGER_REGISTRY,
    RESAMPLE_REGISTRY,
    STEP_JUDGE_REGISTRY,
    STEP_LEN_REGISTRY,
    WEIGHT_REGISTRY,
)
from Geomag.models import PFState
from Geomag.nn import Module, Sequential


@dataclass
class PDRModule:
    step_judge: object
    step_length: object
    heading: object
    mag: object

    def detect_step(self, samples) -> bool:
        return bool(self.step_judge.forward(samples))

    def estimate_step_len(self, samples) -> float:
        return float(self.step_length.forward(samples))

    def estimate_heading(self, samples) -> float:
        return float(self.heading.forward(samples))

    def extract_mag(self) -> float:
        return float(self.mag.forward())


@dataclass
class PFModule:
    stages: Module
    state_kwargs: dict = field(default_factory=dict)

    def step(self, pf_state, step_len: float, heading_angle: float, geomag_seq):
        if getattr(pf_state, "map_points", None) is None:
            return pf_state.get_pos()

        ctx = {
            "pf_state": pf_state,
            "step_len": float(step_len),
            "heading_angle": float(heading_angle),
            "geomag_seq": list(geomag_seq),
            "target_n": len(pf_state.particles),
            "should_resample": False,
        }
        self.stages(ctx)
        return pf_state.get_pos()


@dataclass
class PDRConfig:
    step_judge: str = "peak_dynamic"
    step_judge_params: dict = field(default_factory=dict)
    step_length: str = "weinberg"
    step_length_params: dict = field(default_factory=dict)
    heading: str = "gyro"
    heading_params: dict = field(default_factory=dict)
    mag: str = "norm_mean"
    mag_params: dict = field(default_factory=dict)


@dataclass
class PFConfig:
    motion: str = "gaussian"
    motion_params: dict = field(default_factory=dict)
    weight: str = "ddtw"
    weight_params: dict = field(default_factory=dict)
    particle_size: str = "kld"
    particle_size_params: dict = field(default_factory=dict)
    resample_trigger: str = "ess_or_target"
    resample_trigger_params: dict = field(default_factory=dict)
    resample: str = "cso"
    resample_params: dict = field(default_factory=dict)
    state_params: dict = field(default_factory=dict)
    stage_order: tuple[str, ...] = (
        "predict",
        "update",
        "particle_size",
        "resample_decision",
        "resample",
    )


class PredictStage(Module):
    def __init__(self, motion="gaussian", motion_kwargs=None):
        self.motion = MOTION_REGISTRY.build(motion, **(motion_kwargs or {}))

    def forward(self, ctx):
        self.motion.forward(
            ctx["pf_state"],
            step_len=ctx["step_len"],
            heading_angle=ctx["heading_angle"],
        )
        return ctx


class UpdateStage(Module):
    def __init__(self, weight="ddtw", weight_kwargs=None):
        self.weight = WEIGHT_REGISTRY.build(weight, **(weight_kwargs or {}))

    def forward(self, ctx):
        self.weight.forward(ctx["pf_state"], geomag_seq=ctx["geomag_seq"])
        return ctx


class ParticleSizeStage(Module):
    def __init__(self, particle_size="kld", particle_size_kwargs=None):
        self.particle_size = PARTICLE_SIZE_REGISTRY.build(particle_size, **(particle_size_kwargs or {}))

    def forward(self, ctx):
        ctx["target_n"] = int(self.particle_size.forward(ctx["pf_state"]))
        return ctx


class ResampleDecisionStage(Module):
    def __init__(self, trigger="ess_or_target", trigger_kwargs=None):
        self.trigger = RESAMPLE_TRIGGER_REGISTRY.build(trigger, **(trigger_kwargs or {}))

    def forward(self, ctx):
        hist_len = len(ctx.get("geomag_seq", []) or [])
        ctx["should_resample"] = bool(
            self.trigger.should_resample(
                ctx["pf_state"],
                target_count=int(ctx.get("target_n", 0)),
                hist_len=hist_len,
            )
        )
        return ctx


class ResampleStage(Module):
    def __init__(self, resample="cso", resample_kwargs=None):
        self.resample = RESAMPLE_REGISTRY.build(resample, **(resample_kwargs or {}))

    def forward(self, ctx):
        target_n = int(ctx.get("target_n", len(ctx["pf_state"].particles)))
        should_resample = bool(ctx.get("should_resample", target_n != len(ctx["pf_state"].particles)))
        if should_resample:
            self.resample.forward(ctx["pf_state"], target_count=target_n)
        return ctx


def build_pdr_module(
    step_judge="peak_dynamic",
    step_judge_kwargs=None,
    step_length="weinberg",
    step_length_kwargs=None,
    heading="gyro",
    heading_kwargs=None,
    mag="norm_mean",
    mag_kwargs=None,
):
    return PDRModule(
        step_judge=STEP_JUDGE_REGISTRY.build(step_judge, **(step_judge_kwargs or {})),
        step_length=STEP_LEN_REGISTRY.build(step_length, **(step_length_kwargs or {})),
        heading=HEADING_REGISTRY.build(heading, **(heading_kwargs or {})),
        mag=MAG_REGISTRY.build(mag, **(mag_kwargs or {})),
    )


def build_pdr_from_config(config: PDRConfig | None = None):
    config = config or PDRConfig()
    return build_pdr_module(
        step_judge=config.step_judge,
        step_judge_kwargs=config.step_judge_params,
        step_length=config.step_length,
        step_length_kwargs=config.step_length_params,
        heading=config.heading,
        heading_kwargs=config.heading_params,
        mag=config.mag,
        mag_kwargs=config.mag_params,
    )


def build_pf_module(
    motion="gaussian",
    motion_kwargs=None,
    weight="ddtw",
    weight_kwargs=None,
    resample="cso",
    resample_kwargs=None,
    particle_size="kld",
    particle_size_kwargs=None,
    resample_trigger="ess_or_target",
    resample_trigger_kwargs=None,
    state_kwargs=None,
    stage_order=None,
):
    if stage_order is None:
        stage_order = ("predict", "update", "particle_size", "resample_decision", "resample")
    stage_map = {
        "predict": PredictStage(motion=motion, motion_kwargs=motion_kwargs),
        "update": UpdateStage(weight=weight, weight_kwargs=weight_kwargs),
        "particle_size": ParticleSizeStage(
            particle_size=particle_size,
            particle_size_kwargs=particle_size_kwargs,
        ),
        "resample_decision": ResampleDecisionStage(
            trigger=resample_trigger,
            trigger_kwargs=resample_trigger_kwargs,
        ),
        "resample": ResampleStage(resample=resample, resample_kwargs=resample_kwargs),
    }
    names = [str(s).strip().lower() for s in stage_order]
    unknown = [s for s in names if s not in stage_map]
    if unknown:
        raise ValueError(f"Unknown PF stage(s): {unknown}. Allowed: {sorted(stage_map.keys())}")
    stages = Sequential(*[(name, stage_map[name]) for name in names])
    return PFModule(stages=stages, state_kwargs=dict(state_kwargs or {}))


def build_pf_from_config(config: PFConfig | None = None):
    config = config or PFConfig()
    return build_pf_module(
        motion=config.motion,
        motion_kwargs=config.motion_params,
        weight=config.weight,
        weight_kwargs=config.weight_params,
        particle_size=config.particle_size,
        particle_size_kwargs=config.particle_size_params,
        resample_trigger=config.resample_trigger,
        resample_trigger_kwargs=config.resample_trigger_params,
        resample=config.resample,
        resample_kwargs=config.resample_params,
        state_kwargs=config.state_params,
        stage_order=config.stage_order,
    )


def build_pf_sequential(*stages):
    return PFModule(stages=Sequential(*stages), state_kwargs={})


class GeomagPipeline:
    def __init__(self, context, pdr_module=None, pf_module=None):
        self.context = context
        self.pdr_module = pdr_module or build_pdr_module()
        self.pf_module = pf_module or build_pf_module()

    @staticmethod
    def available_blocks():
        return {
            "step_judge": STEP_JUDGE_REGISTRY.keys(),
            "step_length": STEP_LEN_REGISTRY.keys(),
            "heading": HEADING_REGISTRY.keys(),
            "mag": MAG_REGISTRY.keys(),
            "motion": MOTION_REGISTRY.keys(),
            "weight": WEIGHT_REGISTRY.keys(),
            "resample": RESAMPLE_REGISTRY.keys(),
            "particle_size": PARTICLE_SIZE_REGISTRY.keys(),
            "resample_trigger": RESAMPLE_TRIGGER_REGISTRY.keys(),
            "pf_stage_order_default": ["predict", "update", "particle_size", "resample_decision", "resample"],
            "pf_stage_order_allowed": ["predict", "update", "particle_size", "resample_decision", "resample"],
        }

    @staticmethod
    def describe_configs():
        return {
            "pdr": {
                "step_judge": STEP_JUDGE_REGISTRY.describe(),
                "step_length": STEP_LEN_REGISTRY.describe(),
                "heading": HEADING_REGISTRY.describe(),
                "mag": MAG_REGISTRY.describe(),
                "common_runtime_params": {
                    "step_judge_params": {
                        "peak_sigma": 0.45,
                        "peak_prominence": 0.18,
                        "fixed_threshold": 10.7,
                        "zero_crossing_band": 0.22,
                        "min_samples_per_step": 4,
                        "freq_ratio_threshold": 3.0,
                        "autocorr_threshold": 0.35,
                    },
                    "step_length_params": {
                        "weinberg_k": 0.45,
                        "fixed_step_length_m": 0.7,
                    },
                    "heading_params": {
                        "dt": 0.02,
                    },
                },
            },
            "pf": {
                "motion": MOTION_REGISTRY.describe(),
                "weight": WEIGHT_REGISTRY.describe(),
                "particle_size": PARTICLE_SIZE_REGISTRY.describe(),
                "resample_trigger": RESAMPLE_TRIGGER_REGISTRY.describe(),
                "resample": RESAMPLE_REGISTRY.describe(),
                "stage_order_allowed": ["predict", "update", "particle_size", "resample_decision", "resample"],
                "state_params": {
                    "num_particles": 500,
                    "seed": 42,
                    "weight_sigma": 8.0,
                    "min_particles": 100,
                    "max_particles": 100000000,
                },
            },
        }

    @staticmethod
    def _latlon_to_xy(lat, lon, lat0, lon0):
        radius = 6378137.0
        dlat = np.radians(np.asarray(lat, dtype=float) - float(lat0))
        dlon = np.radians(np.asarray(lon, dtype=float) - float(lon0))
        x = radius * dlon * np.cos(np.radians((np.asarray(lat, dtype=float) + float(lat0)) * 0.5))
        y = radius * dlat
        return np.asarray(x, dtype=float), np.asarray(y, dtype=float)

    @staticmethod
    def _resolve_origin_latlon(geomag_map):
        if not isinstance(geomag_map, dict):
            return None, None

        latlon = geomag_map.get("origin_latlon")
        if isinstance(latlon, (list, tuple)) and len(latlon) >= 2:
            try:
                return float(latlon[0]), float(latlon[1])
            except (TypeError, ValueError):
                pass

        model_path = geomag_map.get("output_model_npz")
        if not model_path:
            return None, None

        try:
            npz_path = Path(model_path)
            if not npz_path.exists():
                return None, None
            with np.load(npz_path) as model:
                if "origin_lat" in model and "origin_lon" in model:
                    return float(model["origin_lat"][0]), float(model["origin_lon"][0])
        except Exception:
            return None, None

        return None, None

    @classmethod
    def _route_to_xy_for_error(cls, route, geomag_map):
        arr = np.asarray(route, dtype=float)
        if arr.ndim != 2 or arr.shape[1] < 2 or arr.shape[0] == 0:
            return None, None

        if isinstance(geomag_map, dict) and geomag_map.get("source") == "own":
            return np.asarray(arr[:, 0], dtype=float), np.asarray(arr[:, 1], dtype=float)

        origin_lat, origin_lon = cls._resolve_origin_latlon(geomag_map)
        if origin_lat is None or origin_lon is None:
            return None, None

        return cls._latlon_to_xy(arr[:, 0], arr[:, 1], origin_lat, origin_lon)

    @staticmethod
    def _compute_error_series(track_xy, route_x, route_y):
        arr = np.asarray(track_xy, dtype=float)
        if arr.ndim != 2 or arr.shape[1] < 2:
            return None

        px = np.asarray(arr[:, 0], dtype=float)
        py = np.asarray(arr[:, 1], dtype=float)
        rx = np.asarray(route_x, dtype=float)
        ry = np.asarray(route_y, dtype=float)
        if px.size == 0 or rx.size == 0 or ry.size == 0:
            return None

        route_idx = np.linspace(0, rx.size - 1, num=px.size)
        route_idx = np.clip(np.rint(route_idx).astype(int), 0, rx.size - 1)
        ref_x = rx[route_idx]
        ref_y = ry[route_idx]
        return np.sqrt((px - ref_x) ** 2 + (py - ref_y) ** 2)

    @staticmethod
    def _summarize_error(series):
        if series is None:
            return None
        arr = np.asarray(series, dtype=float).reshape(-1)
        if arr.size == 0:
            return None
        return {
            "mean": float(np.mean(arr)),
            "median": float(np.median(arr)),
            "p95": float(np.percentile(arr, 95)),
            "final": float(arr[-1]),
        }

    @staticmethod
    def _print_error_summary(name, stats):
        if not stats:
            return
        print(
            f"{name} error [m] | "
            f"mean={stats['mean']:.3f}, median={stats['median']:.3f}, "
            f"p95={stats['p95']:.3f}, final={stats['final']:.3f}"
        )

    def run(self, show=True, output_png=None, max_frames=None):
        geomag_map = self.context.geomag_map
        own_dataset_key = getattr(self.context, "own_dataset_key", None)
        route = get_true_route(
            source=self.context.route_source,
            data_root=self.context.data_root,
            uji_test_file=self.context.uji_test_file,
            own_data_dir=self.context.own_data_dir,
            own_dataset_key=own_dataset_key,
        )
        if not route:
            raise ValueError("True route is empty, cannot initialize particle filter.")

        pf_state = PFState(
            init_pos=route[0],
            mag_map=geomag_map,
            **dict(getattr(self.pf_module, "state_kwargs", {}) or {}),
        )
        pos_list = [pf_state.get_pos()]
        pdr_list = [pf_state.get_pos()]
        particle_counts = [len(pf_state.particles)]
        geomag_list = []
        sample_buffer = []

        test_len = get_test_len(
            source=self.context.sensor_source,
            data_root=self.context.data_root,
            uji_test_file=self.context.uji_test_file,
            own_data_dir=self.context.own_data_dir,
            own_dataset_key=own_dataset_key,
        )
        if max_frames is not None:
            test_len = min(int(max_frames), int(test_len))

        def _print_progress(current, total, width=36):
            total = max(int(total), 1)
            current = min(max(int(current), 0), total)
            ratio = current / total
            filled = int(width * ratio)
            bar = "#" * filled + "-" * (width - filled)
            sys.stdout.write(f"\rProgress [{bar}] {current}/{total} ({ratio * 100:5.1f}%)")
            sys.stdout.flush()

        _print_progress(0, test_len)
        for i in range(test_len):
            mag, acc, gyro = get_sensor(
                source=self.context.sensor_source,
                data_root=self.context.data_root,
                uji_test_file=self.context.uji_test_file,
                own_data_dir=self.context.own_data_dir,
                own_dataset_key=own_dataset_key,
            )
            sample_buffer.append([acc, gyro, mag])

            if not self.pdr_module.detect_step(sample_buffer):
                _print_progress(i + 1, test_len)
                continue

            step_len = self.pdr_module.estimate_step_len(sample_buffer)
            heading_angle = self.pdr_module.estimate_heading(sample_buffer)
            geomag_value = self.pdr_module.extract_mag()
            geomag_list.append(geomag_value)
            geomag_window = geomag_list[-self.context.window_size :]

            pdr_x, pdr_y = pdr_list[-1]
            pdr_list.append(
                (
                    float(pdr_x + step_len * math.cos(heading_angle)),
                    float(pdr_y + step_len * math.sin(heading_angle)),
                )
            )
            pos = self.pf_module.step(
                pf_state=pf_state,
                step_len=step_len,
                heading_angle=heading_angle,
                geomag_seq=geomag_window,
            )
            pos_list.append(pos)
            particle_counts.append(len(pf_state.particles))
            sample_buffer.clear()
            _print_progress(i + 1, test_len)

        sys.stdout.write("\n")
        sys.stdout.flush()

        route_x, route_y = self._route_to_xy_for_error(route, geomag_map)
        pdr_error_series = None
        pf_error_series = None
        pdr_error_stats = None
        pf_error_stats = None
        if route_x is None or route_y is None:
            print("PDR error: skipped (missing map origin to convert route lat/lon into XY meters).")
        else:
            pdr_error_series = self._compute_error_series(pdr_list, route_x, route_y)
            pf_error_series = self._compute_error_series(pos_list, route_x, route_y)
            pdr_error_stats = self._summarize_error(pdr_error_series)
            pf_error_stats = self._summarize_error(pf_error_series)
            self._print_error_summary("PDR", pdr_error_stats)
            self._print_error_summary("PF ", pf_error_stats)

        saved = visualize(
            pos_list=pos_list,
            pdr_list=pdr_list,
            route=route,
            error_series=pf_error_series,
            pdr_error_series=pdr_error_series,
            particle_counts=particle_counts,
            geomag_map=geomag_map,
            mode="ujimap",
            show=show,
            output_png=output_png,
        )
        return {
            "pos_list": pos_list,
            "pdr_list": pdr_list,
            "particle_counts": particle_counts,
            "route": route,
            "output_png": saved,
            "pdr_error_series": pdr_error_series.tolist() if pdr_error_series is not None else None,
            "pf_error_series": pf_error_series.tolist() if pf_error_series is not None else None,
            "pdr_error_stats": pdr_error_stats,
            "pf_error_stats": pf_error_stats,
        }
