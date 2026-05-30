import inspect
import math
from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Any

import numpy as np

from Geomag import algorithms
from Geomag.distance import _ddtw_distance, _wrap_angle_pi


class Registry:
    """Generic name→builder registry (inspired by PyTorch's optimizer registry)."""

    def __init__(self, name: str) -> None:
        self.name = name
        self._builders: dict[str, Callable[..., object]] = {}
        self._param_docs: dict[str, dict[str, object]] = {}

    def register(self, key: str, param_docs: dict | None = None) -> Callable:
        key = str(key).lower()

        def decorator(builder: Callable[..., object]) -> Callable[..., object]:
            self._builders[key] = builder
            self._param_docs[key] = dict(param_docs or {})
            return builder

        return decorator

    def build(self, key: str, **kwargs: Any) -> object:
        token = str(key).lower()
        if token not in self._builders:
            available = ", ".join(sorted(self._builders))
            raise ValueError(f"Unknown {self.name} block '{key}'. Available: [{available}]")
        return self._builders[token](**kwargs)

    def keys(self) -> list[str]:
        return sorted(self._builders.keys())

    def describe(self) -> dict[str, dict[str, dict]]:
        return {
            key: {
                "params": self._param_docs.get(key, {}),
            }
            for key in self.keys()
        }


def describe_callable_params(callable_obj: Callable) -> dict[str, Any]:
    signature = inspect.signature(callable_obj)
    params = {}
    for name, param in signature.parameters.items():
        if name == "self":
            continue
        if param.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
            continue
        default = None if param.default is inspect.Parameter.empty else param.default
        params[name] = default
    return params


class StepJudgeBlock(ABC):
    """Detect whether a buffered sample window contains a completed step."""

    @abstractmethod
    def forward(self, samples: list[list[float]]) -> bool:
        raise NotImplementedError


class StepLengthBlock(ABC):
    """Estimate step length from buffered samples (metres)."""

    @abstractmethod
    def forward(self, samples: list[list[float]]) -> float:
        raise NotImplementedError


class HeadingBlock(ABC):
    """Estimate heading angle from buffered samples (radians)."""

    @abstractmethod
    def forward(self, samples: list[list[float]]) -> float:
        raise NotImplementedError


class MagBlock(ABC):
    """Extract a scalar magnetic feature from the current sensor frame."""

    @abstractmethod
    def forward(self) -> float:
        raise NotImplementedError


class MotionBlock(ABC):
    """Apply a motion model to each particle."""

    @abstractmethod
    def forward(self, pf_state: Any, step_len: float, heading_angle: float) -> None:
        raise NotImplementedError


class WeightBlock(ABC):
    """Update particle weights based on magnetic observation."""

    @abstractmethod
    def forward(self, pf_state: Any, geomag_seq: list[float]) -> None:
        raise NotImplementedError


class ResampleBlock(ABC):
    """Resample the particle set."""

    @abstractmethod
    def forward(self, pf_state: Any, target_count: int) -> None:
        raise NotImplementedError


class ParticleSizeBlock(ABC):
    """Determine the desired number of particles."""

    @abstractmethod
    def forward(self, pf_state: Any) -> int:
        raise NotImplementedError


class ResampleTriggerBlock(ABC):
    """Decide whether resampling should occur."""

    @abstractmethod
    def should_resample(self, pf_state: Any, target_count: int, **kwargs: Any) -> bool:
        raise NotImplementedError


class AlgoStepJudge(StepJudgeBlock):
    def __init__(self, method: str = "peak_dynamic", **kwargs: Any) -> None:
        self.method = method
        self.kwargs = kwargs

    def forward(self, samples: list[list[float]]) -> bool:
        return bool(algorithms.judge_step(samples, method=self.method, **self.kwargs))


class AlgoStepLength(StepLengthBlock):
    def __init__(self, method: str = "weinberg", **kwargs: Any) -> None:
        self.method = method
        self.kwargs = kwargs

    def forward(self, samples: list[list[float]]) -> float:
        return float(algorithms.get_step_len(samples, method=self.method, **self.kwargs))


class AlgoHeading(HeadingBlock):
    def __init__(self, method: str = "q_fused", **kwargs: Any) -> None:
        self.method = method
        self.kwargs = kwargs

    def forward(self, samples: list[list[float]]) -> float:
        return float(algorithms.get_heading_angle(samples, method=self.method, **self.kwargs))


class AlgoMag(MagBlock):
    def __init__(self, method: str = "norm_mean") -> None:
        self.method = method

    def forward(self) -> float:
        return float(algorithms.get_mag(method=self.method))



class GaussianMotion(MotionBlock):
    """Gaussian motion model with configurable boundary handling.

    Parameters
    ----------
    boundary_handling : str
        ``"kill"`` — particles outside strict bounds are killed (legacy).
        ``"clamp"`` — particles are soft-clamped to the nearest boundary.
    """

    def __init__(self, heading_noise_std: float = 0.12, step_noise_std: float = 0.22,
                 boundary_handling: str = "kill") -> None:
        self.heading_noise_std = float(heading_noise_std)
        self.step_noise_std = float(step_noise_std)
        self.boundary_handling = str(boundary_handling).lower()

    def forward(self, pf_state: Any, step_len: float, heading_angle: float) -> None:
        rng = getattr(pf_state, "rng", np.random.default_rng(42))
        for p in pf_state.particles:
            if not getattr(p, "alive", True):
                continue
            theta = _wrap_angle_pi(float(heading_angle) + float(rng.normal(0.0, self.heading_noise_std)))
            dist = max(0.0, float(step_len) + float(rng.normal(0.0, self.step_noise_std)))
            p.theta = theta
            nx = float(p.x + dist * math.cos(theta))
            ny = float(p.y + dist * math.sin(theta))
            if not pf_state.in_strict_map_bounds(nx, ny):
                if self.boundary_handling == "clamp":
                    nx, ny = pf_state.clamp_to_strict_map(nx, ny)
                else:
                    pf_state.kill_particle(p)
                    continue
            p.x, p.y = float(nx), float(ny)


class DDTWWeight(WeightBlock):
    """DDTW-based particle weight update.

    Parameters
    ----------
    accumulate_mode : str
        ``"multiply"`` — multiplicative accumulation (legacy).
        ``"average"`` — EMA: ``w = alpha*prior + (1-alpha)*current``.
        ``"max"`` — ``w = max(prior, current)`` (forgiving).
    alpha : float
        EMA smoothing factor (only used when ``accumulate_mode="average"``).
    """

    def __init__(self, sigma: float | None = None, max_hist: int = 100,
                 window_ratio: float = 0.25, instant_sigma: float | None = None,
                 accumulate: bool = True, accumulate_mode: str = "multiply",
                 alpha: float = 0.7) -> None:
        self.sigma = sigma
        self.max_hist = int(max_hist)
        self.window_ratio = float(window_ratio)
        self.instant_sigma = instant_sigma
        self.accumulate = bool(accumulate)
        self.accumulate_mode = str(accumulate_mode).lower()
        self.alpha = float(np.clip(alpha, 0.0, 1.0))

    def forward(self, pf_state: Any, geomag_seq: list[float]) -> None:
        obs = np.asarray(list(geomag_seq), dtype=float).reshape(-1)
        sigma = float(self.sigma if self.sigma is not None else getattr(pf_state, "weight_sigma", 8.0))
        instant_sigma = (
            float(self.instant_sigma)
            if self.instant_sigma is not None
            else None
        )

        for p in pf_state.particles:
            if not getattr(p, "alive", True):
                p.weight = 0.0
                continue
            prior_weight = float(max(p.weight, 1e-12)) if self.accumulate else 1.0
            pred_mag = float(pf_state.map_magnitude(p.x, p.y))
            if not math.isfinite(pred_mag):
                pf_state.kill_particle(p)
                continue
            p.mag_hist.append(pred_mag)
            hist_len = int(max(1, min(len(obs), self.max_hist)))
            pred_seq = np.asarray(p.mag_hist[-hist_len:], dtype=float)
            obs_seq = obs[-hist_len:] if obs.size else np.asarray([pred_mag], dtype=float)
            d = _ddtw_distance(obs_seq, pred_seq, window_ratio=self.window_ratio)
            w_ddtw = math.exp(-((d * d) / (2.0 * sigma * sigma + 1e-12)))
            if instant_sigma is not None and instant_sigma > 1e-8 and obs_seq.size > 0:
                residual = float(abs(pred_mag - float(obs_seq[-1])))
                w_inst = math.exp(-((residual * residual) / (2.0 * instant_sigma * instant_sigma + 1e-12)))
                weight = w_ddtw * w_inst
            else:
                weight = w_ddtw

            weight = float(max(1e-12, weight))

            if not self.accumulate:
                p.weight = weight
            elif self.accumulate_mode == "average":
                p.weight = float(self.alpha * prior_weight + (1.0 - self.alpha) * weight)
            elif self.accumulate_mode == "max":
                p.weight = float(max(prior_weight, weight))
            else:  # "multiply" (legacy)
                p.weight = float(max(1e-12, prior_weight * weight))
        pf_state._normalize_weights()


class CSOResample(ResampleBlock):
    def forward(self, pf_state: Any, target_count: int) -> None:
        pf_state.cso_resample(target_count=target_count)


class SystematicResample(ResampleBlock):
    """Systematic resampling with diversity injection.

    Parameters
    ----------
    inject_ratio : float
        Fraction of particles replaced with random samples (0.0–1.0).
    noise_scale : float
        Gaussian noise std added to copied particles (metres).
    """

    def __init__(self, inject_ratio: float = 0.05, noise_scale: float = 0.15) -> None:
        self.inject_ratio = float(np.clip(inject_ratio, 0.0, 1.0))
        self.noise_scale = float(max(0.0, noise_scale))

    def forward(self, pf_state: Any, target_count: int) -> None:
        pf_state.systematic_resample(
            target_count=target_count,
            inject_ratio=self.inject_ratio,
            noise_scale=self.noise_scale,
        )


class KLDSampleSize(ParticleSizeBlock):
    def __init__(self, epsilon: float = 0.12, z: float = 1.96,
                 bin_size_xy: float = 0.8, bin_size_theta: float = 0.35) -> None:
        self.epsilon = float(epsilon)
        self.z = float(z)
        self.bin_size_xy = float(bin_size_xy)
        self.bin_size_theta = float(bin_size_theta)

    def forward(self, pf_state: Any) -> int:
        return int(
            pf_state.adapt_particle_count_kld(
                epsilon=self.epsilon,
                z=self.z,
                bin_size_xy=self.bin_size_xy,
                bin_size_theta=self.bin_size_theta,
            )
        )


class ESSOrTargetTrigger(ResampleTriggerBlock):
    def __init__(self, ess_ratio_threshold: float = 0.5, warmup_steps: int = 8,
                 min_weight_cv: float = 0.01, flat_ess_ratio: float = 0.95) -> None:
        self.ess_ratio_threshold = float(ess_ratio_threshold)
        self.warmup_steps = int(max(0, warmup_steps))
        self.min_weight_cv = float(max(0.0, min_weight_cv))
        self.flat_ess_ratio = float(np.clip(flat_ess_ratio, 0.0, 1.0))

    @staticmethod
    def _weight_cv(pf_state: Any) -> float:
        ws = np.asarray([max(float(p.weight), 0.0) for p in pf_state.particles], dtype=float)
        if ws.size == 0:
            return 0.0
        total = float(np.sum(ws))
        if total <= 1e-12:
            return 0.0
        wn = ws / total
        mu = float(np.mean(wn))
        if mu <= 1e-12:
            return 0.0
        return float(np.std(wn) / (mu + 1e-12))

    def should_resample(self, pf_state: Any, target_count: int, hist_len: int | None = None, **kwargs: Any) -> bool:
        curr_n = max(1, len(pf_state.particles))
        ess = float(pf_state.effective_sample_size())
        if ess < self.ess_ratio_threshold * curr_n:
            return True

        if int(target_count) == curr_n:
            return False

        if hist_len is not None and int(hist_len) < self.warmup_steps:
            return False

        ess_ratio = ess / float(curr_n)
        weight_cv = self._weight_cv(pf_state)
        if ess_ratio >= self.flat_ess_ratio and weight_cv < self.min_weight_cv:
            return False

        return True


class AlwaysTrigger(ResampleTriggerBlock):
    def should_resample(self, pf_state: Any, target_count: int, **kwargs: Any) -> bool:
        return True


STEP_JUDGE_REGISTRY = Registry("step_judge")
STEP_LEN_REGISTRY = Registry("step_length")
HEADING_REGISTRY = Registry("heading")
MAG_REGISTRY = Registry("mag")
MOTION_REGISTRY = Registry("motion")
WEIGHT_REGISTRY = Registry("weight")
RESAMPLE_REGISTRY = Registry("resample")
PARTICLE_SIZE_REGISTRY = Registry("particle_size")
RESAMPLE_TRIGGER_REGISTRY = Registry("resample_trigger")


@STEP_JUDGE_REGISTRY.register("peak_dynamic", param_docs=describe_callable_params(AlgoStepJudge.__init__))
def _build_step_judge_peak_dynamic(**kwargs):
    return AlgoStepJudge(method="peak_dynamic", **kwargs)


@STEP_JUDGE_REGISTRY.register("peak_fixed", param_docs=describe_callable_params(AlgoStepJudge.__init__))
def _build_step_judge_peak_fixed(**kwargs):
    return AlgoStepJudge(method="peak_fixed", **kwargs)


@STEP_JUDGE_REGISTRY.register("zero_crossing", param_docs=describe_callable_params(AlgoStepJudge.__init__))
def _build_step_judge_zero_crossing(**kwargs):
    return AlgoStepJudge(method="zero_crossing", **kwargs)


@STEP_JUDGE_REGISTRY.register("valley_peak", param_docs=describe_callable_params(AlgoStepJudge.__init__))
def _build_step_judge_valley_peak(**kwargs):
    return AlgoStepJudge(method="valley_peak", **kwargs)


@STEP_JUDGE_REGISTRY.register("frequency_fft", param_docs=describe_callable_params(AlgoStepJudge.__init__))
def _build_step_judge_frequency_fft(**kwargs):
    return AlgoStepJudge(method="frequency_fft", **kwargs)


@STEP_JUDGE_REGISTRY.register("autocorr", param_docs=describe_callable_params(AlgoStepJudge.__init__))
def _build_step_judge_autocorr(**kwargs):
    return AlgoStepJudge(method="autocorr", **kwargs)


@STEP_LEN_REGISTRY.register("weinberg", param_docs=describe_callable_params(AlgoStepLength.__init__))
def _build_step_len_weinberg(**kwargs):
    return AlgoStepLength(method="weinberg", **kwargs)


@STEP_LEN_REGISTRY.register("fixed", param_docs=describe_callable_params(AlgoStepLength.__init__))
def _build_step_len_fixed(**kwargs):
    return AlgoStepLength(method="fixed", **kwargs)


@HEADING_REGISTRY.register("q_fused", param_docs=describe_callable_params(AlgoHeading.__init__))
def _build_heading_q_fused(**kwargs):
    return AlgoHeading(method="q_fused", **kwargs)


@HEADING_REGISTRY.register("tilt_compass", param_docs=describe_callable_params(AlgoHeading.__init__))
def _build_heading_tilt_compass(**kwargs):
    return AlgoHeading(method="tilt_compass", **kwargs)


@HEADING_REGISTRY.register("gyro", param_docs=describe_callable_params(AlgoHeading.__init__))
def _build_heading_gyro(**kwargs):
    return AlgoHeading(method="gyro", **kwargs)


@MAG_REGISTRY.register("norm_mean", param_docs=describe_callable_params(AlgoMag.__init__))
def _build_mag_norm_mean(**kwargs):
    return AlgoMag(method="norm_mean")


@MAG_REGISTRY.register("norm_last", param_docs=describe_callable_params(AlgoMag.__init__))
def _build_mag_norm_last(**kwargs):
    return AlgoMag(method="norm_last")


@MOTION_REGISTRY.register("gaussian", param_docs=describe_callable_params(GaussianMotion.__init__))
def _build_motion_gaussian(**kwargs):
    return GaussianMotion(**kwargs)


@WEIGHT_REGISTRY.register("ddtw", param_docs=describe_callable_params(DDTWWeight.__init__))
def _build_weight_ddtw(**kwargs):
    return DDTWWeight(**kwargs)


@RESAMPLE_REGISTRY.register("cso", param_docs={})
def _build_resample_cso(**kwargs):
    return CSOResample()


@RESAMPLE_REGISTRY.register("systematic", param_docs=describe_callable_params(SystematicResample.__init__))
def _build_resample_systematic(**kwargs):
    return SystematicResample(**kwargs)


@PARTICLE_SIZE_REGISTRY.register("kld", param_docs=describe_callable_params(KLDSampleSize.__init__))
def _build_particle_size_kld(**kwargs):
    return KLDSampleSize(**kwargs)


@RESAMPLE_TRIGGER_REGISTRY.register("ess_or_target", param_docs=describe_callable_params(ESSOrTargetTrigger.__init__))
def _build_resample_trigger_ess_or_target(**kwargs):
    return ESSOrTargetTrigger(**kwargs)


@RESAMPLE_TRIGGER_REGISTRY.register("always", param_docs={})
def _build_resample_trigger_always(**kwargs):
    return AlwaysTrigger()
