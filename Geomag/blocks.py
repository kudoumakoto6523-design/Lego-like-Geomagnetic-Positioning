import math
import inspect
from abc import ABC, abstractmethod
from typing import Callable

import numpy as np

from Geomag import algorithms


class Registry:
    def __init__(self, name: str):
        self.name = name
        self._builders: dict[str, Callable[..., object]] = {}
        self._param_docs: dict[str, dict[str, object]] = {}

    def register(self, key: str, param_docs=None):
        key = str(key).lower()

        def decorator(builder: Callable[..., object]):
            self._builders[key] = builder
            self._param_docs[key] = dict(param_docs or {})
            return builder

        return decorator

    def build(self, key: str, **kwargs):
        token = str(key).lower()
        if token not in self._builders:
            available = ", ".join(sorted(self._builders))
            raise ValueError(f"Unknown {self.name} block '{key}'. Available: [{available}]")
        return self._builders[token](**kwargs)

    def keys(self):
        return sorted(self._builders.keys())

    def describe(self):
        return {
            key: {
                "params": self._param_docs.get(key, {}),
            }
            for key in self.keys()
        }


def describe_callable_params(callable_obj):
    signature = inspect.signature(callable_obj)
    params = {}
    for name, param in signature.parameters.items():
        if name == "self":
            continue
        if param.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
            continue
        default = None if param.default is inspect._empty else param.default
        params[name] = default
    return params


class StepJudgeBlock(ABC):
    @abstractmethod
    def forward(self, samples) -> bool:
        raise NotImplementedError


class StepLengthBlock(ABC):
    @abstractmethod
    def forward(self, samples) -> float:
        raise NotImplementedError


class HeadingBlock(ABC):
    @abstractmethod
    def forward(self, samples) -> float:
        raise NotImplementedError


class MagBlock(ABC):
    @abstractmethod
    def forward(self) -> float:
        raise NotImplementedError


class MotionBlock(ABC):
    @abstractmethod
    def forward(self, pf_state, step_len: float, heading_angle: float):
        raise NotImplementedError


class WeightBlock(ABC):
    @abstractmethod
    def forward(self, pf_state, geomag_seq):
        raise NotImplementedError


class ResampleBlock(ABC):
    @abstractmethod
    def forward(self, pf_state, target_count: int):
        raise NotImplementedError


class ParticleSizeBlock(ABC):
    @abstractmethod
    def forward(self, pf_state) -> int:
        raise NotImplementedError


class ResampleTriggerBlock(ABC):
    @abstractmethod
    def should_resample(self, pf_state, target_count: int, **kwargs) -> bool:
        raise NotImplementedError


class AlgoStepJudge(StepJudgeBlock):
    def __init__(self, method="peak_dynamic", **kwargs):
        self.method = method
        self.kwargs = kwargs

    def forward(self, samples) -> bool:
        return bool(algorithms.judge_step(samples, method=self.method, **self.kwargs))


class AlgoStepLength(StepLengthBlock):
    def __init__(self, method="weinberg", **kwargs):
        self.method = method
        self.kwargs = kwargs

    def forward(self, samples) -> float:
        return float(algorithms.get_step_len(samples, method=self.method, **self.kwargs))


class AlgoHeading(HeadingBlock):
    def __init__(self, method="q_fused", **kwargs):
        self.method = method
        self.kwargs = kwargs

    def forward(self, samples) -> float:
        return float(algorithms.get_heading_angle(samples, method=self.method, **self.kwargs))


class AlgoMag(MagBlock):
    def __init__(self, method="norm_mean"):
        self.method = method

    def forward(self) -> float:
        return float(algorithms.get_mag(method=self.method))


def _derivative_sequence(x):
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


def _zscore(x):
    x = np.asarray(x, dtype=float).reshape(-1)
    if x.size == 0:
        return x
    mu = float(np.mean(x))
    sd = float(np.std(x))
    if sd < 1e-8:
        return x - mu
    return (x - mu) / sd


def _ddtw_distance(a, b, window_ratio=0.25):
    a = _zscore(_derivative_sequence(a))
    b = _zscore(_derivative_sequence(b))
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


def _wrap_angle_pi(rad):
    return float(((rad + math.pi) % (2.0 * math.pi)) - math.pi)


class GaussianMotion(MotionBlock):
    def __init__(self, heading_noise_std=0.12, step_noise_std=0.22):
        self.heading_noise_std = float(heading_noise_std)
        self.step_noise_std = float(step_noise_std)

    def forward(self, pf_state, step_len: float, heading_angle: float):
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
                pf_state.kill_particle(p)
                continue
            p.x, p.y = float(nx), float(ny)


class DDTWWeight(WeightBlock):
    def __init__(self, sigma=None, max_hist=100, window_ratio=0.25, instant_sigma=None, accumulate=True):
        self.sigma = sigma
        self.max_hist = int(max_hist)
        self.window_ratio = float(window_ratio)
        self.instant_sigma = instant_sigma
        self.accumulate = bool(accumulate)

    def forward(self, pf_state, geomag_seq):
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
            p.weight = float(max(1e-12, prior_weight * weight))
        pf_state._normalize_weights()


class CSOResample(ResampleBlock):
    def forward(self, pf_state, target_count: int):
        pf_state.cso_resample(target_count=target_count)


class KLDSampleSize(ParticleSizeBlock):
    def __init__(self, epsilon=0.12, z=1.96, bin_size_xy=0.8, bin_size_theta=0.35):
        self.epsilon = float(epsilon)
        self.z = float(z)
        self.bin_size_xy = float(bin_size_xy)
        self.bin_size_theta = float(bin_size_theta)

    def forward(self, pf_state) -> int:
        return int(
            pf_state.adapt_particle_count_kld(
                epsilon=self.epsilon,
                z=self.z,
                bin_size_xy=self.bin_size_xy,
                bin_size_theta=self.bin_size_theta,
            )
        )


class ESSOrTargetTrigger(ResampleTriggerBlock):
    def __init__(self, ess_ratio_threshold=0.5, warmup_steps=8, min_weight_cv=0.01, flat_ess_ratio=0.95):
        self.ess_ratio_threshold = float(ess_ratio_threshold)
        self.warmup_steps = int(max(0, warmup_steps))
        self.min_weight_cv = float(max(0.0, min_weight_cv))
        self.flat_ess_ratio = float(np.clip(flat_ess_ratio, 0.0, 1.0))

    @staticmethod
    def _weight_cv(pf_state) -> float:
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

    def should_resample(self, pf_state, target_count: int, hist_len=None, **kwargs) -> bool:
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
    def should_resample(self, pf_state, target_count: int, **kwargs) -> bool:
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


@PARTICLE_SIZE_REGISTRY.register("kld", param_docs=describe_callable_params(KLDSampleSize.__init__))
def _build_particle_size_kld(**kwargs):
    return KLDSampleSize(**kwargs)


@RESAMPLE_TRIGGER_REGISTRY.register("ess_or_target", param_docs=describe_callable_params(ESSOrTargetTrigger.__init__))
def _build_resample_trigger_ess_or_target(**kwargs):
    return ESSOrTargetTrigger(**kwargs)


@RESAMPLE_TRIGGER_REGISTRY.register("always", param_docs={})
def _build_resample_trigger_always(**kwargs):
    return AlwaysTrigger()
