"""LLM-assisted parameter tuning for geomagnetic PDR/PF experiments.

DeepSeek only proposes a small JSON parameter patch.  This module validates
every path, type and range before the patch can reach an experiment config; it
never executes model-generated code.
"""

from __future__ import annotations

import copy
import json
import math
import os
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np

from Geomag.pipeline import PDRConfig, PFConfig


SYSTEM_PROMPT = """
你是一名以 PDR 为首要优化对象的地磁定位参数工程师。用户会给你一次或多次真实实验的 JSON 数据，
其中包含 RTK Ground Truth、PDR 轨迹、PF 预测轨迹、误差统计、地图覆盖率、当前参数及历史试验。

最高优先级目标是先降低 PDR 相对 RTK 的 mean/p95/final 误差，并修正 PDR 的步数、路程尺度、航向和
转弯形态；只有 PDR 已经稳定、或证据明确表明剩余问题来自粒子滤波时，才把 PF 参数作为第二优先级。
不得依靠收紧 PF 参数来掩盖尚未解决的 PDR 漂移。第一轮默认只调整 PDR；后续轮次如果 PDR 误差仍在
明显改善或仍存在系统性偏差，parameter_patch.pf 必须保持为空对象。

请先独立比较 RTK 与 PDR：步数异常检查 peak_sigma、peak_prominence 和 min_samples_per_step；PDR 路程
相对 RTK 过长或过短时检查 Weinberg 系数；整体旋转、方向相反或转弯后产生系统性偏差时检查
heading_offset_deg。完成 PDR 诊断后，再比较 PF 与 PDR；仅当 PDR 已基本收敛但 PF 仍未有效利用地磁信息
时，才检查磁匹配 sigma、运动噪声、重采样阈值、KLD 参数和粒子数。

最终目标是在 PDR 可靠的基础上降低未参与建图的 RTK 测试路线上的 PF mean/p95/final 误差，同时避免
粒子数无必要膨胀和只对单次轨迹过拟合。

你只能建议 allowed_parameters 中列出的参数，不能修改算法名称、代码、数据、RTK 真值或地图，不能把
Ground Truth 当作推理阶段的输入。每轮采用小而可解释的改动，历史试验没有证据时不要大幅改变多个参数。
粒子数必须始终满足 min_particles <= num_particles <= max_particles。
所有输出必须是一个合法 JSON object，不能包含 Markdown 或额外文字。

输出 JSON 格式示例：
{
  "optimization_stage": "pdr_primary",
  "pdr_diagnosis": "步数、距离尺度和航向误差的判断",
  "pf_diagnosis": "PDR 未收敛，本轮暂不调整 PF",
  "analysis_summary": "先给出 RTK-PDR 误差诊断，再说明是否需要处理 PF",
  "parameter_patch": {
    "pdr": {"step_length_params": {"weinberg_k": 0.48}},
    "pf": {}
  },
  "expected_effect": "预期改善",
  "risk_notes": ["可能风险"],
  "stop_tuning": false
}
""".strip()


@dataclass(frozen=True)
class DeepSeekSettings:
    api_key: str = ""
    base_url: str = "https://api.deepseek.com"
    model: str = "deepseek-v4-flash"
    timeout_s: float = 120.0
    max_tokens: int = 1800
    max_trajectory_points: int = 160
    temperature: float = 0.2

    @classmethod
    def load(cls, config_path="config/deepseek_auto_tuning.json"):
        raw = {}
        path = Path(config_path)
        if path.exists():
            raw = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            api_key=str(os.getenv("DEEPSEEK_API_KEY") or raw.get("api_key") or "").strip(),
            base_url=str(os.getenv("DEEPSEEK_BASE_URL") or raw.get("base_url") or cls.base_url).rstrip("/"),
            model=str(os.getenv("DEEPSEEK_MODEL") or raw.get("model") or cls.model),
            timeout_s=float(raw.get("timeout_s", cls.timeout_s)),
            max_tokens=int(raw.get("max_tokens", cls.max_tokens)),
            max_trajectory_points=int(
                raw.get("max_trajectory_points", cls.max_trajectory_points)
            ),
            temperature=float(raw.get("temperature", cls.temperature)),
        )

    @property
    def configured(self):
        token = self.api_key.strip()
        return bool(token and token not in {"YOUR_DEEPSEEK_API_KEY", "<YOUR_DEEPSEEK_API_KEY>"})

    def public_dict(self):
        return {
            "api_key_configured": self.configured,
            "base_url": self.base_url,
            "model": self.model,
            "timeout_s": self.timeout_s,
            "max_tokens": self.max_tokens,
            "max_trajectory_points": self.max_trajectory_points,
            "temperature": self.temperature,
        }


class DeepSeekConfigurationError(RuntimeError):
    pass


class DeepSeekAPIError(RuntimeError):
    pass


# path -> (type, minimum, maximum). The model cannot change anything else.
TUNABLE_PARAMETER_SPECS = {
    "pdr.step_judge_params.peak_sigma": (float, 0.05, 3.0),
    "pdr.step_judge_params.peak_prominence": (float, 0.01, 5.0),
    "pdr.step_judge_params.min_samples_per_step": (int, 3, 120),
    "pdr.step_length_params.weinberg_k": (float, 0.10, 1.50),
    "pdr.heading_params.heading_offset_deg": (float, -180.0, 180.0),
    "pf.state_params.num_particles": (int, 300, 30000),
    "pf.state_params.min_particles": (int, 100, 20000),
    "pf.state_params.max_particles": (int, 500, 1000000),
    "pf.motion_params.heading_noise_std": (float, 0.001, 0.60),
    "pf.motion_params.step_noise_std": (float, 0.001, 1.50),
    "pf.weight_params.sigma": (float, 0.05, 30.0),
    "pf.weight_params.max_hist": (int, 5, 500),
    "pf.particle_size_params.epsilon": (float, 0.01, 1.0),
    "pf.particle_size_params.bin_size_xy": (float, 0.02, 3.0),
    "pf.particle_size_params.bin_size_theta": (float, 0.02, 3.2),
    "pf.resample_trigger_params.ess_ratio_threshold": (float, 0.05, 0.95),
}


def allowed_parameter_description():
    return {
        path: {
            "type": "integer" if spec[0] is int else "number",
            "minimum": spec[1],
            "maximum": spec[2],
        }
        for path, spec in TUNABLE_PARAMETER_SPECS.items()
    }


def configs_to_dict(pdr_config, pf_config):
    return {
        "pdr": asdict(pdr_config),
        "pf": asdict(pf_config),
    }


def _as_track(value):
    arr = np.asarray(value if value is not None else [], dtype=float)
    if arr.ndim != 2 or arr.shape[0] == 0 or arr.shape[1] < 2:
        return np.empty((0, 2), dtype=float)
    return arr[:, :2]


def _sample_track(track, progress):
    if track.shape[0] == 0:
        return np.full((len(progress), 2), np.nan, dtype=float)
    source_progress = np.linspace(0.0, 1.0, track.shape[0])
    return np.column_stack(
        [np.interp(progress, source_progress, track[:, axis]) for axis in range(2)]
    )


def _track_length(track):
    if track.shape[0] < 2:
        return 0.0
    return float(np.linalg.norm(np.diff(track, axis=0), axis=1).sum())


def _round_optional(value, digits=6):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return round(number, digits) if math.isfinite(number) else None


def build_tuning_observation(result, pdr_config, pf_config, max_points=160):
    """Build a compact payload containing genuine RTK/PDR/PF coordinates."""
    route = _as_track(result.get("route_xy_m"))
    pdr_value = result.get("pdr_list")
    if pdr_value is None:
        pdr_value = result.get("pdr_track")
    predicted_value = result.get("pos_list")
    if predicted_value is None:
        predicted_value = result.get("pf_track")
    pdr = _as_track(pdr_value)
    predicted = _as_track(predicted_value)
    if route.shape[0] == 0 or pdr.shape[0] == 0 or predicted.shape[0] == 0:
        raise ValueError("Tuning requires non-empty RTK, PDR, and PF trajectories")

    sample_count = min(max(8, int(max_points)), max(route.shape[0], pdr.shape[0], predicted.shape[0]))
    progress = np.linspace(0.0, 1.0, sample_count)
    route_sample = _sample_track(route, progress)
    pdr_sample = _sample_track(pdr, progress)
    predicted_sample = _sample_track(predicted, progress)
    pdr_error = np.linalg.norm(pdr_sample - route_sample, axis=1)
    pf_error = np.linalg.norm(predicted_sample - route_sample, axis=1)

    comparison = []
    for index in range(sample_count):
        comparison.append(
            {
                "progress": round(float(progress[index]), 6),
                "rtk_xy_m": [round(float(value), 6) for value in route_sample[index]],
                "pdr_xy_m": [round(float(value), 6) for value in pdr_sample[index]],
                "pf_xy_m": [round(float(value), 6) for value in predicted_sample[index]],
                "pdr_error_m": round(float(pdr_error[index]), 6),
                "pf_error_m": round(float(pf_error[index]), 6),
            }
        )

    return {
        "experiment": {
            "branch": result.get("branch"),
            "navigation_key": result.get("outdoor_navigation_key"),
            "sensor_frames_used": result.get("sensor_frames_used"),
            "steps_detected": result.get("steps_detected"),
            "route_inside_map_percent": result.get("used_route_inside_map_percent"),
            "map_bounds": result.get("map_bounds"),
        },
        "error_stats": {
            "pdr": result.get("pdr_error_stats"),
            "pf": result.get("pf_error_stats"),
        },
        "trajectory_summary": {
            "rtk_points": int(route.shape[0]),
            "pdr_points": int(pdr.shape[0]),
            "pf_points": int(predicted.shape[0]),
            "rtk_path_length_m": round(_track_length(route), 6),
            "pdr_path_length_m": round(_track_length(pdr), 6),
            "pf_path_length_m": round(_track_length(predicted), 6),
            "direct_samples": comparison,
        },
        "current_parameters": configs_to_dict(pdr_config, pf_config),
        "allowed_parameters": allowed_parameter_description(),
    }


def build_deepseek_messages(observation, tuning_history=None):
    user_payload = {
        "optimization_priority": [
            "PDR step detection",
            "PDR step length",
            "PDR heading",
            "PF parameters only after PDR convergence",
        ],
        "instruction": (
            "先根据 RTK 与 PDR 的真实轨迹和历史试验诊断并调整 PDR 参数；"
            "PDR 未收敛时不要调整 PF。输出必须是 json object，parameter_patch "
            "只能使用 allowed_parameters，第一轮的 pf patch 应为空对象。"
        ),
        "current_experiment": observation,
        "tuning_history": list(tuning_history or []),
    }
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
    ]


def _flatten_patch(value, prefix=""):
    items = {}
    if not isinstance(value, dict):
        return items
    for key, child in value.items():
        path = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(child, dict):
            items.update(_flatten_patch(child, path))
        else:
            items[path] = child
    return items


def _nest_patch(flat):
    nested = {}
    for path, value in flat.items():
        cursor = nested
        parts = path.split(".")
        for part in parts[:-1]:
            cursor = cursor.setdefault(part, {})
        cursor[parts[-1]] = value
    return nested


def validate_parameter_patch(suggestion):
    proposed = suggestion.get("parameter_patch", {}) if isinstance(suggestion, dict) else {}
    accepted = {}
    rejected = []
    for path, value in _flatten_patch(proposed).items():
        spec = TUNABLE_PARAMETER_SPECS.get(path)
        if spec is None:
            rejected.append({"path": path, "value": value, "reason": "not_whitelisted"})
            continue
        value_type, minimum, maximum = spec
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            rejected.append({"path": path, "value": value, "reason": "not_numeric"})
            continue
        if not math.isfinite(numeric) or numeric < minimum or numeric > maximum:
            rejected.append({"path": path, "value": value, "reason": "outside_allowed_range"})
            continue
        accepted[path] = int(round(numeric)) if value_type is int else float(numeric)
    if not accepted and not bool(suggestion.get("stop_tuning", False)):
        raise ValueError(f"DeepSeek returned no valid tunable parameters; rejected={rejected}")
    return _nest_patch(accepted), rejected


def _merge_config_section(config, section_name, values):
    current = dict(getattr(config, section_name) or {})
    current.update(values)
    setattr(config, section_name, current)


def apply_parameter_patch(pdr_config, pf_config, validated_patch):
    pdr = copy.deepcopy(pdr_config)
    pf = copy.deepcopy(pf_config)
    for section_name, values in validated_patch.get("pdr", {}).items():
        _merge_config_section(pdr, section_name, values)
    for section_name, values in validated_patch.get("pf", {}).items():
        _merge_config_section(pf, section_name, values)

    state = pf.state_params
    minimum = int(state.get("min_particles", 100))
    count = int(state.get("num_particles", max(minimum, 300)))
    maximum = int(state.get("max_particles", max(count, minimum)))
    if not minimum <= count <= maximum:
        raise ValueError(
            "Invalid DeepSeek particle-count patch: require min_particles <= num_particles <= max_particles"
        )
    return pdr, pf


def tuning_score(result):
    pf_stats = result.get("pf_error_stats") or {}
    pdr_stats = result.get("pdr_error_stats") or {}
    mean = _round_optional(pf_stats.get("mean"))
    p95 = _round_optional(pf_stats.get("p95"))
    pdr_mean = _round_optional(pdr_stats.get("mean"))
    if mean is None or p95 is None:
        return float("inf")
    return float(mean + 0.25 * p95 + 0.05 * (pdr_mean if pdr_mean is not None else mean))


class DeepSeekTuningClient:
    def __init__(self, settings=None):
        self.settings = settings or DeepSeekSettings.load()

    def _request_once(self, messages):
        if not self.settings.configured:
            raise DeepSeekConfigurationError(
                "DeepSeek API key is blank. Set DEEPSEEK_API_KEY or fill the local tuning config."
            )
        payload = {
            "model": self.settings.model,
            "messages": messages,
            "response_format": {"type": "json_object"},
            "thinking": {"type": "disabled"},
            "temperature": self.settings.temperature,
            "max_tokens": self.settings.max_tokens,
            "stream": False,
        }
        request = urllib.request.Request(
            f"{self.settings.base_url}/chat/completions",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.settings.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.settings.timeout_s) as response:
                raw = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:1000]
            raise DeepSeekAPIError(f"DeepSeek HTTP {exc.code}: {detail}") from exc
        except (OSError, json.JSONDecodeError) as exc:
            raise DeepSeekAPIError(f"DeepSeek request failed: {exc}") from exc

        try:
            content = raw["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise DeepSeekAPIError("DeepSeek response has no assistant content") from exc
        if not str(content or "").strip():
            return None, raw.get("usage", {})
        try:
            return json.loads(content), raw.get("usage", {})
        except json.JSONDecodeError as exc:
            raise DeepSeekAPIError("DeepSeek JSON Output was not parseable") from exc

    def suggest(self, observation, tuning_history=None):
        messages = build_deepseek_messages(observation, tuning_history=tuning_history)
        suggestion, usage = self._request_once(messages)
        if suggestion is None:
            # Official documentation notes that JSON mode can rarely be empty.
            suggestion, usage = self._request_once(messages)
        if suggestion is None:
            raise DeepSeekAPIError("DeepSeek returned empty JSON content twice")
        validated_patch, rejected = validate_parameter_patch(suggestion)
        return {
            "suggestion": suggestion,
            "validated_patch": validated_patch,
            "rejected_parameters": rejected,
            "usage": usage,
        }


def suggest_parameters(
    result,
    pdr_config,
    pf_config,
    tuning_history=None,
    settings=None,
):
    """Public backend interface for one DeepSeek-driven tuning proposal."""
    client = DeepSeekTuningClient(settings=settings)
    observation = build_tuning_observation(
        result,
        pdr_config,
        pf_config,
        max_points=client.settings.max_trajectory_points,
    )
    response = client.suggest(observation, tuning_history=tuning_history)
    response["observation"] = observation
    return response
