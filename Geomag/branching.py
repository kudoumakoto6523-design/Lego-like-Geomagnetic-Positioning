import copy
import json
import math
import sys
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable

import numpy as np

from Geomag import Experiment, Initializer, PFConfig, PDRConfig, build_pdr_from_config, build_pf_from_config
from Geomag.algorithms import get_map, get_sensor, get_test_len, get_true_route
from Geomag.models import PFState
from Geomag.outdoor_navigation import (
    load_outdoor_navigation_session,
    resolve_outdoor_navigation,
)
from Geomag.own_dataset_registry import available_own_dataset_keys, get_own_dataset_spec
from Geomag.pipeline import GeomagPipeline


@dataclass
class BranchConfig:
    branch: str = "uji"
    window_size: int = 400
    max_frames: int | None = None
    show: bool = True
    output_json: str | None = None
    output_png: str | None = None
    progress_callback: Callable[[int, int, str], None] | None = None

    # UJI branch.
    uji_test_file: str = "tt02.txt"
    uji_data_root: str = "data/raw"

    # Own-data branch.
    own_profile: str = "own_branch"
    own_dataset_key: str = "route1_run1"
    own_data_dir: str = "data/own_data/Geomagnetic Navigation 2026-03-19 20-10-45"
    own_map_mode: str = "raw"
    own_map_npz_path: str | None = None
    own_route_xy_m: list[list[float]] | None = None
    own_initial_heading_deg: float | None = None
    own_use_route_initial_heading: bool = True
    own_mirror_y: bool = False
    own_heading_offset_deg: float = -90.0
    own_trim_head: int = 0
    own_trim_tail: int = 0

    # Outdoor RTK branch.
    outdoor_navigation_key: str = "nav1"
    outdoor_data_root: str = "data/raw/outdoor_rtk_map"
    outdoor_initial_heading_deg: float | None = None
    outdoor_use_route_initial_heading: bool = True
    outdoor_heading_offset_deg: float = 0.0
    outdoor_auto_tune: bool = False
    outdoor_tune_iterations: int = 3
    outdoor_tune_dry_run: bool = False
    outdoor_tune_config_path: str = "config/deepseek_auto_tuning.json"
    outdoor_deepseek_api_key: str | None = None


DEFAULT_UJI_DATA_ROOT = "data/raw"
DEFAULT_OWN_BRANCH_DATA_DIR = "data/own_data/Geomagnetic Navigation 2026-03-19 20-10-45"
DEFAULT_OUTDOOR_DATA_ROOT = "data/raw/outdoor_rtk_map"


def resolve_uji_selection(selection):
    token = str(selection or "tt02").strip()
    if not token:
        token = "tt02"

    path = Path(token)
    if path.suffix:
        test_file = token
    elif token.isdigit():
        test_file = f"tt{int(token):02d}.txt"
    elif token.lower().startswith("tt"):
        test_file = f"{token}.txt"
    else:
        test_file = token

    return {
        "uji_test_file": test_file,
        "uji_data_root": DEFAULT_UJI_DATA_ROOT,
    }


def resolve_own_selection(selection):
    token = str(selection or "own_branch").strip()
    if not token:
        token = "own_branch"

    if token in available_own_dataset_keys():
        return {
            "own_profile": "package",
            "own_dataset_key": token,
            "own_data_dir": get_own_dataset_spec(token)["dataset_dir"],
        }

    if token in {"own_branch", "legacy", "web"}:
        return {
            "own_profile": "own_branch",
            "own_dataset_key": "own_branch",
            "own_data_dir": DEFAULT_OWN_BRANCH_DATA_DIR,
        }

    return {
        "own_profile": "own_branch",
        "own_dataset_key": Path(token).name or "own_branch",
        "own_data_dir": token,
    }


def resolve_outdoor_selection(selection):
    return resolve_outdoor_navigation(
        selection=selection or "nav1",
        data_root=DEFAULT_OUTDOOR_DATA_ROOT,
    )


def build_uji_configs():
    pdr_config = PDRConfig(
        step_judge="peak_dynamic",
        step_judge_params={
            "peak_sigma": 0.4,
            "peak_prominence": 0.2,
            "min_samples_per_step": 4.5,
        },
        step_length="weinberg",
        step_length_params={"weinberg_k": 0.45},
        heading="gyro",
        heading_params={"dt": 0.01},
        mag="norm_mean",
    )
    pf_config = PFConfig(
        state_params={
            "num_particles": 5000,
            "min_particles": 2000,
            "max_particles": 10000000000000,
        },
        motion="gaussian",
        motion_params={"heading_noise_std": 0.01, "step_noise_std": 0.01},
        weight="ddtw",
        weight_params={"sigma": 0.1, "max_hist": 100},
        particle_size="kld",
        particle_size_params={"epsilon": 0.10, "bin_size_xy": 0.5, "bin_size_theta": 0.35},
        resample_trigger="ess_or_target",
        resample_trigger_params={"ess_ratio_threshold": 0.40},
        resample="cso",
    )
    return pdr_config, pf_config


def build_own_package_configs():
    # Own data needs a less aggressive magnetic likelihood than the UJI defaults.
    pdr_config = PDRConfig(
        step_judge="peak_dynamic",
        step_judge_params={
            "peak_sigma": 0.45,
            "peak_prominence": 0.30,
            "min_samples_per_step": 15,
        },
        step_length="weinberg",
        step_length_params={"weinberg_k": 0.45},
        heading="gyro",
        heading_params={"dt": None},
        mag="norm_mean",
    )
    pf_config = PFConfig(
        state_params={
            "num_particles": 5000,
            "min_particles": 2000,
            "max_particles": 10000000000000,
        },
        motion="gaussian",
        motion_params={"heading_noise_std": 0.01, "step_noise_std": 0.01},
        weight="ddtw",
        weight_params={"sigma": 0.5, "max_hist": 100},
        particle_size="kld",
        particle_size_params={"epsilon": 0.10, "bin_size_xy": 0.5, "bin_size_theta": 0.35},
        resample_trigger="ess_or_target",
        resample_trigger_params={"ess_ratio_threshold": 0.40},
        resample="cso",
    )
    return pdr_config, pf_config


def build_own_branch_configs():
    pdr_config = PDRConfig(
        step_judge="peak_dynamic",
        step_judge_params={
            "peak_sigma": 0.45,
            "peak_prominence": 0.30,
            "min_samples_per_step": 15,
        },
        step_length="weinberg",
        step_length_params={"weinberg_k": 0.45},
        heading="gyro",
        heading_params={"dt": None, "alpha": 0.90},
        mag="norm_mean",
    )
    pf_config = PFConfig(
        state_params={"num_particles": 5000},
        weight_params={"sigma": 0.5},
    )
    return pdr_config, pf_config


def build_outdoor_configs():
    pdr_config = PDRConfig(
        step_judge="peak_dynamic",
        step_judge_params={
            "peak_sigma": 0.45,
            "peak_prominence": 0.30,
            "min_samples_per_step": 15,
        },
        step_length="weinberg",
        step_length_params={"weinberg_k": 0.45},
        heading="gyro",
        heading_params={"dt": None},
        mag="norm_mean",
    )
    pf_config = PFConfig(
        state_params={
            "num_particles": 3000,
            "min_particles": 1000,
            "max_particles": 1000000,
        },
        motion="gaussian",
        motion_params={"heading_noise_std": 0.03, "step_noise_std": 0.05},
        weight="ddtw",
        weight_params={"sigma": 1.5, "max_hist": 100},
        particle_size="kld",
        particle_size_params={"epsilon": 0.10, "bin_size_xy": 0.25, "bin_size_theta": 0.35},
        resample_trigger="ess_or_target",
        resample_trigger_params={"ess_ratio_threshold": 0.40},
        resample="cso",
    )
    return pdr_config, pf_config


def build_own_configs(profile="own_branch"):
    token = str(profile).strip().lower()
    if token in {"own_branch", "legacy", "web"}:
        return build_own_branch_configs()
    if token in {"package", "registry"}:
        return build_own_package_configs()
    raise ValueError(f"Unsupported own profile: {profile}. Use 'own_branch' or 'package'.")


def build_configs(branch: str):
    token = str(branch).strip().lower()
    if token == "uji":
        return build_uji_configs()
    if token == "own":
        return build_own_configs()
    if token == "outdoor":
        return build_outdoor_configs()
    raise ValueError(f"Unsupported branch: {branch}. Use 'uji', 'own', or 'outdoor'.")


def build_own_tile_matrix(raw_matrix, mode="raw", rows=8, cols=12):
    arr = np.asarray(raw_matrix, dtype=float)
    if arr.ndim != 2 or arr.shape[0] == 0 or arr.shape[1] == 0:
        raise ValueError("Own magnetic map input must be a non-empty 2D matrix.")

    token = str(mode).strip().lower()
    if token == "raw":
        return arr
    if token not in {"tile12", "tile_12"}:
        raise ValueError(f"Unsupported own map mode: {mode}. Use 'raw' or 'tile12'.")

    ridx = np.linspace(0, arr.shape[0] - 1, rows, dtype=int)
    cidx = np.linspace(0, arr.shape[1] - 1, cols, dtype=int)
    return arr[ridx][:, cidx]


def _load_first_2d_array_from_npz(path):
    data = np.load(path, allow_pickle=True)
    if not hasattr(data, "files"):
        raise ValueError(f"Own map npz has no named arrays: {path}")
    for key in data.files:
        arr = np.asarray(data[key])
        if arr.ndim == 2 and arr.size > 0:
            return np.asarray(arr, dtype=float)
    raise ValueError(f"Own map npz contains no valid 2D grid: {path}")


def build_own_geomag_map(config: BranchConfig):
    profile = str(config.own_profile).strip().lower()
    default_npz = "data/own_data/my_mag_map.npz" if profile in {"own_branch", "legacy", "web"} else None
    npz_path = config.own_map_npz_path or default_npz
    if npz_path:
        grid = _load_first_2d_array_from_npz(npz_path)
        return get_map(
            source="own",
            own_grid_array=grid,
            own_grid_meta={
                "tile_size_x_m": 0.02,
                "tile_size_y_m": 0.02,
                "anchor": "corner",
                "flip_y": False,
                "origin_xy_m": [0.0, 0.0],
                "force_tile_matrix": True,
            },
        )

    from data.own_data.magnetometer_map_own import data as own_map_raw

    tile_matrix = build_own_tile_matrix(own_map_raw, mode=config.own_map_mode, rows=8, cols=12)
    rows, cols = tile_matrix.shape
    if str(config.own_map_mode).strip().lower() == "raw":
        tile_size_x_m = 11.52 / float(cols)
        tile_size_y_m = 8.80 / float(rows)
    else:
        tile_size_x_m = 0.96
        tile_size_y_m = 1.10

    return get_map(
        source="own",
        own_grid_array=tile_matrix,
        own_grid_meta={
            "tile_size_x_m": tile_size_x_m,
            "tile_size_y_m": tile_size_y_m,
            "anchor": "center",
            "flip_y": True,
            "origin_xy_m": [0.0, 0.0],
        },
    )


def parse_route_control_points(route_text: str):
    points = []
    for pair in str(route_text or "").split(";"):
        token = pair.strip()
        if not token or "," not in token:
            continue
        x_str, y_str = token.split(",", 1)
        points.append([float(x_str.strip()), float(y_str.strip())])
    if len(points) < 2:
        raise ValueError("Route control points must contain at least two x,y pairs.")
    return points


def default_own_branch_route_controls():
    return parse_route_control_points("0.96,0; 0.96,5.05; 3.84,5.05")


def densify_route_controls(route_xy, step_size=0.05):
    pts = _polyline_points(route_xy)
    dense = []
    step = max(float(step_size), 1e-6)
    for i in range(pts.shape[0] - 1):
        p1 = pts[i]
        p2 = pts[i + 1]
        dist = float(np.linalg.norm(p2 - p1))
        n = max(2, int(dist / step))
        endpoint = i == pts.shape[0] - 2
        xs = np.linspace(float(p1[0]), float(p2[0]), n, endpoint=endpoint)
        ys = np.linspace(float(p1[1]), float(p2[1]), n, endpoint=endpoint)
        for x, y in zip(xs, ys):
            dense.append([float(x), float(y)])
    return dense


def _polyline_points(route_xy):
    arr = np.asarray(route_xy, dtype=float)
    if arr.ndim != 2 or arr.shape[0] < 2 or arr.shape[1] < 2:
        raise ValueError("route_xy must be shape (N, 2) with N >= 2.")
    return arr[:, :2]


def _polyline_cumulative(route_xy):
    pts = _polyline_points(route_xy)
    seg = np.linalg.norm(np.diff(pts, axis=0), axis=1)
    cum = np.concatenate([[0.0], np.cumsum(seg)])
    total = float(cum[-1])
    if total <= 1e-9:
        raise ValueError("Route polyline length is zero.")
    return pts, cum, total


def sample_route_segment(route_xy, start_frac=0.0, end_frac=1.0, n=300):
    pts, cum, total = _polyline_cumulative(route_xy)
    start_frac = float(np.clip(start_frac, 0.0, 1.0))
    end_frac = float(np.clip(end_frac, 0.0, 1.0))
    if end_frac <= start_frac:
        raise ValueError("end_frac must be larger than start_frac.")

    dist = np.linspace(start_frac * total, end_frac * total, max(2, int(n)), dtype=float)
    x = np.interp(dist, cum, pts[:, 0])
    y = np.interp(dist, cum, pts[:, 1])
    return np.column_stack([x, y])


def corrected_own_heading(heading_angle, mirror_y=True, heading_offset_deg=0.0):
    ang = float(heading_angle) + math.radians(float(heading_offset_deg))
    if mirror_y:
        ang = -ang
    return float(((ang + math.pi) % (2.0 * math.pi)) - math.pi)


def infer_initial_heading_from_route(route_xy, min_displacement_m=1e-9):
    pts = _polyline_points(route_xy)
    threshold = max(float(min_displacement_m), 1e-9)
    for i in range(1, pts.shape[0]):
        dx = float(pts[i, 0] - pts[0, 0])
        dy = float(pts[i, 1] - pts[0, 1])
        if math.hypot(dx, dy) >= threshold:
            return float(math.atan2(dy, dx))
    # Preserve the old behavior for very short routes while preferring a
    # displacement large enough to suppress centimetre-level RTK jitter.
    for i in range(pts.shape[0] - 1, 0, -1):
        dx = float(pts[i, 0] - pts[0, 0])
        dy = float(pts[i, 1] - pts[0, 1])
        if math.hypot(dx, dy) > 1e-9:
            return float(math.atan2(dy, dx))
    raise ValueError("Cannot infer initial heading from a zero-length route.")


def summarize_error(track, route, geomag_map):
    route_x, route_y = GeomagPipeline._route_to_xy_for_error(route, geomag_map)
    if route_x is None or route_y is None:
        return None, None
    series = GeomagPipeline._compute_error_series(track, route_x, route_y)
    stats = GeomagPipeline._summarize_error(series)
    return series, stats


def save_own_trajectory_plot(geomag_map, route, pdr_list, pf_list, output_png, show=False):
    try:
        import matplotlib.pyplot as plt
    except ImportError:
        return None

    fig, ax = plt.subplots(figsize=(8, 6), dpi=130)
    z = np.asarray(geomag_map.get("grid_array"), dtype=float)
    if z.ndim == 2 and z.size > 0:
        meta = geomag_map.get("grid_map_contract", {}).get("meta", {})
        flip_y = bool(meta.get("flip_y", True))
        z_plot = np.flipud(z) if flip_y else z
        ax.imshow(
            z_plot,
            origin="lower",
            extent=[
                geomag_map["rangex_min"],
                geomag_map["rangex_max"],
                geomag_map["rangey_min"],
                geomag_map["rangey_max"],
            ],
            aspect="equal",
            cmap="viridis",
            alpha=0.9,
        )

    route_arr = np.asarray(route, dtype=float)
    pdr_arr = np.asarray(pdr_list, dtype=float)
    pf_arr = np.asarray(pf_list, dtype=float)

    if route_arr.ndim == 2 and route_arr.shape[1] >= 2:
        ax.plot(route_arr[:, 0], route_arr[:, 1], "w-", linewidth=2.0, label="route")
        ax.scatter([route_arr[0, 0]], [route_arr[0, 1]], c="lime", s=30, zorder=3, label="route_start")
    if pdr_arr.ndim == 2 and pdr_arr.shape[1] >= 2:
        ax.plot(pdr_arr[:, 0], pdr_arr[:, 1], "--", color="orange", linewidth=1.5, label="pdr")
    if pf_arr.ndim == 2 and pf_arr.shape[1] >= 2:
        ax.plot(pf_arr[:, 0], pf_arr[:, 1], "-", color="cyan", linewidth=1.8, label="pf")

    ax.set_title("Own Simulation")
    ax.set_xlabel("X (m)")
    ax.set_ylabel("Y (m)")
    ax.grid(True, alpha=0.2)
    ax.legend(loc="best")
    ax.set_aspect("equal")
    fig.tight_layout()

    out = Path(output_png)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, bbox_inches="tight")
    if show:
        plt.show()
    plt.close(fig)
    return str(out)


def _print_progress(current, total, width=36):
    total = max(int(total), 1)
    current = min(max(int(current), 0), total)
    ratio = current / total
    filled = int(width * ratio)
    bar = "#" * filled + "-" * (width - filled)
    sys.stdout.write(f"\rProgress [{bar}] {current}/{total} ({ratio * 100:5.1f}%)")
    sys.stdout.flush()


def _report_progress(config: BranchConfig, current: int, total: int, phase: str = "PF / PDR 定位计算"):
    """Report CLI and optional UI progress without coupling the backend to Bokeh."""
    _print_progress(current, total)
    callback = config.progress_callback
    if callback is not None:
        try:
            callback(int(current), int(total), str(phase))
        except Exception:
            # A disconnected UI must not interrupt a simulation that can still
            # finish and write its result files.
            pass


def _jsonable(value: Any):
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value


def _write_json(path, payload):
    if not path:
        return None
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(_jsonable(payload), indent=2), encoding="utf-8")
    return str(out)


def run_uji_branch(config: BranchConfig):
    pdr_config, pf_config = build_uji_configs()
    context = Initializer(
        num_runs=1,
        window_size=config.window_size,
        route_source="uji",
        sensor_source="uji",
        data_root=config.uji_data_root,
        uji_test_file=config.uji_test_file,
    ).create_context()

    result = Experiment(context, pdr_config=pdr_config, pf_config=pf_config).run(
        show=config.show,
        output_png=config.output_png,
        max_frames=config.max_frames,
        progress_callback=config.progress_callback,
    )
    result["branch"] = "uji"
    result["uji_test_file"] = config.uji_test_file
    if config.output_json:
        result["output_json"] = _write_json(config.output_json, result)
        print(f"Saved JSON: {result['output_json']}")
    return result


def run_own_branch(config: BranchConfig):
    profile = str(config.own_profile).strip().lower()
    use_registry = profile in {"package", "registry"}
    if profile not in {"own_branch", "legacy", "web", "package", "registry"}:
        raise ValueError(f"Unsupported own profile: {config.own_profile}. Use 'own_branch' or 'package'.")

    if use_registry:
        dataset_spec = get_own_dataset_spec(config.own_dataset_key)
        own_dataset_key = config.own_dataset_key
        own_data_dir = config.own_data_dir
        route_full = config.own_route_xy_m or get_true_route(source="own", own_dataset_key=own_dataset_key)
    else:
        own_dataset_key = None
        own_data_dir = config.own_data_dir
        dataset_spec = {
            "key": "own_branch",
            "dataset_dir": str(Path(own_data_dir).resolve()),
            "route_label": "manual_controls",
        }
        route_controls = config.own_route_xy_m or default_own_branch_route_controls()
        route_full = densify_route_controls(route_controls, step_size=0.05)

    geomag_map = build_own_geomag_map(config)
    if not route_full:
        raise ValueError("Own route is empty.")

    full_frames = int(get_test_len(source="own", own_data_dir=own_data_dir, own_dataset_key=own_dataset_key))
    head = max(0, int(config.own_trim_head))
    tail = max(0, int(config.own_trim_tail))
    usable_frames = full_frames - head - tail
    if usable_frames <= 1:
        raise ValueError(
            f"Invalid own trim config: total={full_frames}, trim_head={head}, trim_tail={tail}."
        )

    total_frames = usable_frames if config.max_frames is None else min(usable_frames, int(config.max_frames))
    start_frac = head / float(full_frames)
    end_frac = (head + total_frames) / float(full_frames)
    if profile in {"own_branch", "legacy", "web"} and head == 0 and tail == 0 and config.max_frames is None:
        route = np.asarray(route_full, dtype=float)
    else:
        route = sample_route_segment(route_full, start_frac=start_frac, end_frac=end_frac, n=max(100, total_frames // 2))

    pdr_config, pf_config = build_own_configs(profile=profile)
    if config.own_initial_heading_deg is not None:
        initial_heading_rad = math.radians(float(config.own_initial_heading_deg))
    elif config.own_use_route_initial_heading:
        initial_heading_rad = infer_initial_heading_from_route(route)
    else:
        initial_heading_rad = None
    pdr_config.heading_params = dict(pdr_config.heading_params or {})
    pdr_config.heading_params.update(
        {
            "initial_heading_rad": initial_heading_rad,
            "heading_offset_deg": config.own_heading_offset_deg,
        }
    )
    pdr_module = build_pdr_from_config(pdr_config)
    pf_module = build_pf_from_config(pf_config)

    pf_state = PFState(init_pos=[float(route[0, 0]), float(route[0, 1])], mag_map=geomag_map, **dict(pf_config.state_params or {}))
    pf_list = [pf_state.get_pos()]
    pdr_list = [pf_state.get_pos()]
    particle_counts = [len(pf_state.particles)]
    geomag_hist = []
    sample_buffer = []

    for _ in range(head):
        get_sensor(source="own", own_data_dir=own_data_dir, own_dataset_key=own_dataset_key)

    _report_progress(config, 0, total_frames)
    for frame_idx in range(total_frames):
        mag, acc, gyro = get_sensor(source="own", own_data_dir=own_data_dir, own_dataset_key=own_dataset_key)
        sample_buffer.append([acc, gyro, mag])
        if not pdr_module.detect_step(sample_buffer):
            _report_progress(config, frame_idx + 1, total_frames)
            continue

        step_len = float(pdr_module.estimate_step_len(sample_buffer))
        heading_raw = float(pdr_module.estimate_heading(sample_buffer))
        heading_angle = corrected_own_heading(heading_raw, mirror_y=True) if config.own_mirror_y else heading_raw
        obs_mag = float(pdr_module.extract_mag())
        geomag_hist.append(obs_mag)
        geomag_window = geomag_hist[-config.window_size :]

        last_px, last_py = pdr_list[-1]
        pdr_list.append(
            (
                float(last_px + step_len * math.cos(heading_angle)),
                float(last_py + step_len * math.sin(heading_angle)),
            )
        )
        pf_xy = pf_module.step(
            pf_state=pf_state,
            step_len=step_len,
            heading_angle=heading_angle,
            geomag_seq=geomag_window,
        )
        pf_list.append((float(pf_xy[0]), float(pf_xy[1])))
        particle_counts.append(len(pf_state.particles))
        sample_buffer.clear()
        _report_progress(config, frame_idx + 1, total_frames)

    sys.stdout.write("\n")
    sys.stdout.flush()

    pdr_series, pdr_stats = summarize_error(pdr_list, route, geomag_map)
    pf_series, pf_stats = summarize_error(pf_list, route, geomag_map)

    output_png = config.output_png or f"results/branch_own_{config.own_dataset_key}.png"
    plot_path = save_own_trajectory_plot(
        geomag_map=geomag_map,
        route=route,
        pdr_list=pdr_list,
        pf_list=pf_list,
        output_png=output_png,
        show=config.show,
    )

    payload = {
        "branch": "own",
        "own_profile": profile,
        "dataset_key": dataset_spec["key"],
        "dataset_dir": dataset_spec["dataset_dir"],
        "route_label": dataset_spec["route_label"],
        "map_mode": str(config.own_map_mode),
        "map_npz_path": config.own_map_npz_path,
        "mirror_y": bool(config.own_mirror_y),
        "initial_heading_rad": None if initial_heading_rad is None else float(initial_heading_rad),
        "initial_heading_deg": None if initial_heading_rad is None else float(math.degrees(initial_heading_rad)),
        "use_route_initial_heading": bool(config.own_use_route_initial_heading),
        "heading_offset_deg": float(config.own_heading_offset_deg),
        "trim_head": int(head),
        "trim_tail": int(tail),
        "full_sensor_frames": int(full_frames),
        "sensor_frames_used": int(total_frames),
        "steps_detected": max(0, len(pf_list) - 1),
        "map_point_cloud_mode": geomag_map.get("point_cloud_mode"),
        "map_point_cloud_shape": geomag_map.get("point_cloud_shape"),
        "map_bounds": [
            geomag_map.get("rangex_min"),
            geomag_map.get("rangex_max"),
            geomag_map.get("rangey_min"),
            geomag_map.get("rangey_max"),
        ],
        "route_len": int(len(route)),
        "pdr_error_stats": pdr_stats,
        "pf_error_stats": pf_stats,
        "pdr_error_series": None if pdr_series is None else np.asarray(pdr_series, dtype=float).tolist(),
        "pf_error_series": None if pf_series is None else np.asarray(pf_series, dtype=float).tolist(),
        "pdr_track": [list(map(float, xy)) for xy in pdr_list],
        "pf_track": [list(map(float, xy)) for xy in pf_list],
        "particle_counts": [int(x) for x in particle_counts],
        "route_xy_m": [list(map(float, xy)) for xy in route],
        "output_png": plot_path,
    }

    output_json = config.output_json or f"results/branch_own_{config.own_dataset_key}.json"
    payload["output_json"] = _write_json(output_json, payload)

    print("=== OWN BRANCH DONE ===")
    print(f"dataset_key: {dataset_spec['key']}")
    print(f"map_mode: {config.own_map_mode}")
    print(f"steps_detected: {payload['steps_detected']}")
    print(f"pf_error_stats: {pf_stats}")
    print(f"pdr_error_stats: {pdr_stats}")
    print(f"saved_json: {payload['output_json']}")
    if plot_path:
        print(f"saved_plot: {plot_path}")

    return payload


def run_outdoor_branch(
    config: BranchConfig,
    pdr_config_override: PDRConfig | None = None,
    pf_config_override: PFConfig | None = None,
):
    navigation = load_outdoor_navigation_session(
        selection=config.outdoor_navigation_key,
        data_root=config.outdoor_data_root,
    )
    geomag_map = get_map(
        source="outdoor",
        outdoor_data_root=config.outdoor_data_root,
    )
    route = navigation["route_latlon"]
    route_x, route_y = GeomagPipeline._route_to_xy_for_error(route, geomag_map)
    if route_x is None or route_y is None:
        raise ValueError("Unable to convert outdoor RTK route into the map ENU coordinate system.")
    route_xy = np.column_stack([route_x, route_y])
    map_bounds = np.asarray(
        [
            geomag_map.get("rangex_min"),
            geomag_map.get("rangex_max"),
            geomag_map.get("rangey_min"),
            geomag_map.get("rangey_max"),
        ],
        dtype=float,
    )
    route_inside_map = (
        (route_xy[:, 0] >= map_bounds[0])
        & (route_xy[:, 0] <= map_bounds[1])
        & (route_xy[:, 1] >= map_bounds[2])
        & (route_xy[:, 1] <= map_bounds[3])
    )

    default_pdr_config, default_pf_config = build_outdoor_configs()
    pdr_config = copy.deepcopy(pdr_config_override or default_pdr_config)
    pf_config = copy.deepcopy(pf_config_override or default_pf_config)
    if config.outdoor_initial_heading_deg is not None:
        initial_heading_rad = math.radians(float(config.outdoor_initial_heading_deg))
    elif config.outdoor_use_route_initial_heading:
        initial_heading_rad = infer_initial_heading_from_route(
            route_xy,
            min_displacement_m=0.5,
        )
    else:
        initial_heading_rad = None
    pdr_config.heading_params = dict(pdr_config.heading_params or {})
    heading_offset_deg = float(
        pdr_config.heading_params.get(
            "heading_offset_deg",
            config.outdoor_heading_offset_deg,
        )
    )
    pdr_config.heading_params.update(
        {
            "initial_heading_rad": initial_heading_rad,
            "heading_offset_deg": heading_offset_deg,
        }
    )

    context = Initializer(
        num_runs=1,
        window_size=config.window_size,
        route_source="outdoor",
        sensor_source="outdoor",
        outdoor_data_root=config.outdoor_data_root,
        outdoor_navigation_key=config.outdoor_navigation_key,
        geomag_map=geomag_map,
    ).create_context()
    output_png = config.output_png or f"results/branch_outdoor_{navigation['outdoor_navigation_key']}.png"
    result = Experiment(context, pdr_config=pdr_config, pf_config=pf_config).run(
        show=config.show,
        output_png=output_png,
        max_frames=config.max_frames,
        progress_callback=config.progress_callback,
    )
    sensor_frames_used = min(
        navigation["sensor_frames_synchronized"],
        int(config.max_frames)
        if config.max_frames is not None
        else navigation["sensor_frames_synchronized"],
    )
    result.update(
        {
            "branch": "outdoor",
            "outdoor_navigation_key": navigation["outdoor_navigation_key"],
            "outdoor_navigation_zip": navigation["outdoor_navigation_zip"],
            "outdoor_data_root": navigation["outdoor_data_root"],
            "sensor_frames_raw": navigation["sensor_frames_raw"],
            "sensor_frames_synchronized": navigation["sensor_frames_synchronized"],
            "sensor_frames_used": sensor_frames_used,
            "rtk_source_records": navigation["rtk_source_records"],
            "rtk_window_records": navigation["rtk_window_records"],
            "rtk_required_fix_quality": navigation["required_fix_quality"],
            "initial_heading_rad": None if initial_heading_rad is None else float(initial_heading_rad),
            "initial_heading_deg": None if initial_heading_rad is None else float(math.degrees(initial_heading_rad)),
            "heading_offset_deg": heading_offset_deg,
            "steps_detected": max(0, len(result.get("pos_list", [])) - 1),
            "route_xy_m": route_xy[:sensor_frames_used].tolist(),
            "map_bounds": map_bounds.tolist(),
            "route_inside_map_frames": int(route_inside_map.sum()),
            "route_inside_map_percent": float(100.0 * route_inside_map.mean()),
            "used_route_inside_map_percent": float(
                100.0 * route_inside_map[:sensor_frames_used].mean()
            ),
        }
    )
    output_json = config.output_json or f"results/branch_outdoor_{navigation['outdoor_navigation_key']}.json"
    result["output_json"] = _write_json(output_json, result)

    print("=== OUTDOOR BRANCH DONE ===")
    print(f"navigation_key: {navigation['outdoor_navigation_key']}")
    print(f"sensor_frames_used: {result['sensor_frames_used']}")
    print(f"steps_detected: {result['steps_detected']}")
    print(f"route_inside_map: {result['route_inside_map_percent']:.1f}%")
    print(f"pf_error_stats: {result.get('pf_error_stats')}")
    print(f"pdr_error_stats: {result.get('pdr_error_stats')}")
    print(f"saved_json: {result['output_json']}")
    print(f"saved_plot: {result.get('output_png')}")
    return result


def run_outdoor_auto_tuning(config: BranchConfig):
    from Geomag.auto_tuning import (
        DeepSeekConfigurationError,
        DeepSeekSettings,
        DeepSeekTuningClient,
        apply_parameter_patch,
        build_deepseek_messages,
        build_tuning_observation,
        configs_to_dict,
        tuning_score,
    )

    settings = DeepSeekSettings.load(config.outdoor_tune_config_path)
    if config.outdoor_deepseek_api_key:
        # The web UI may provide a session-only key.  Keep it out of config
        # files and reports; DeepSeekSettings.public_dict() never exposes it.
        settings = replace(settings, api_key=config.outdoor_deepseek_api_key.strip())
    if not settings.configured and not config.outdoor_tune_dry_run:
        raise DeepSeekConfigurationError(
            "DeepSeek API key is blank. Set DEEPSEEK_API_KEY, then run auto tuning again."
        )
    client = DeepSeekTuningClient(settings)
    pdr_config, pf_config = build_outdoor_configs()
    pdr_config.heading_params = dict(pdr_config.heading_params or {})
    pdr_config.heading_params["heading_offset_deg"] = float(
        config.outdoor_heading_offset_deg
    )
    iteration_count = max(1, min(int(config.outdoor_tune_iterations), 20))
    tuning_root = (
        Path("data/processed/outdoor_rtk_map/auto_tuning")
        / str(config.outdoor_navigation_key)
    )
    tuning_root.mkdir(parents=True, exist_ok=True)

    history = []
    trial_records = []
    best_result = None
    best_score = float("inf")
    best_trial = -1
    planned_trials = 1 if config.outdoor_tune_dry_run else iteration_count + 1

    def emit_tuning_progress(current: int, total: int, phase: str) -> None:
        callback = config.progress_callback
        if callback is None:
            return
        try:
            callback(int(current), int(total), str(phase))
        except Exception:
            pass

    for trial_index in range(iteration_count + 1):
        # Reserve the tail of every non-final trial for the DeepSeek request so
        # the web progress bar represents the whole tuning job, not a sequence
        # of independent 0-100% simulation runs.
        is_final_trial = config.outdoor_tune_dry_run or trial_index >= iteration_count
        simulation_span = 1000 if is_final_trial else 850

        def trial_progress(current: int, total: int, phase: str, *, _trial=trial_index) -> None:
            total = max(int(total), 1)
            within_trial = int(round(simulation_span * int(current) / total))
            emit_tuning_progress(
                _trial * 1000 + within_trial,
                planned_trials * 1000,
                f"自动调参 {_trial + 1}/{planned_trials} · {phase}",
            )

        trial_config = replace(
            config,
            outdoor_auto_tune=False,
            outdoor_tune_dry_run=False,
            progress_callback=trial_progress,
            show=False,
            output_json=str(tuning_root / f"trial_{trial_index:02d}.json"),
            output_png=str(tuning_root / f"trial_{trial_index:02d}.png"),
        )
        result = run_outdoor_branch(
            trial_config,
            pdr_config_override=pdr_config,
            pf_config_override=pf_config,
        )
        score = tuning_score(result)
        observation = build_tuning_observation(
            result,
            pdr_config,
            pf_config,
            max_points=settings.max_trajectory_points,
        )
        trial_record = {
            "trial": trial_index,
            "score": score,
            "error_stats": observation["error_stats"],
            "parameters": configs_to_dict(pdr_config, pf_config),
            "output_json": result.get("output_json"),
            "output_png": result.get("output_png"),
        }
        trial_records.append(trial_record)
        if score < best_score:
            best_score = score
            best_trial = trial_index
            best_result = result

        if config.outdoor_tune_dry_run:
            request_path = tuning_root / "deepseek_request_preview.json"
            request_path.write_text(
                json.dumps(
                    {
                        "settings": settings.public_dict(),
                        "messages": build_deepseek_messages(observation, tuning_history=history),
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            result["auto_tuning"] = {
                "status": "dry_run",
                "deepseek_called": False,
                "request_preview": str(request_path),
                "settings": settings.public_dict(),
            }
            return result

        if trial_index >= iteration_count:
            break
        emit_tuning_progress(
            trial_index * 1000 + 900,
            planned_trials * 1000,
            f"自动调参 {trial_index + 1}/{planned_trials} · DeepSeek 正在分析轨迹与误差",
        )
        proposal = client.suggest(observation, tuning_history=history)
        trial_record["deepseek"] = {
            "suggestion": proposal["suggestion"],
            "validated_patch": proposal["validated_patch"],
            "rejected_parameters": proposal["rejected_parameters"],
            "usage": proposal["usage"],
        }
        history.append(
            {
                "trial": trial_index,
                "score": score,
                "error_stats": observation["error_stats"],
                "parameters": configs_to_dict(pdr_config, pf_config),
                "accepted_patch_for_next_trial": proposal["validated_patch"],
            }
        )
        if bool(proposal["suggestion"].get("stop_tuning", False)):
            emit_tuning_progress(
                (trial_index + 1) * 1000,
                planned_trials * 1000,
                "DeepSeek 建议提前结束，正在整理最佳结果",
            )
            break
        pdr_config, pf_config = apply_parameter_patch(
            pdr_config,
            pf_config,
            proposal["validated_patch"],
        )
        emit_tuning_progress(
            (trial_index + 1) * 1000,
            planned_trials * 1000,
            f"自动调参 {trial_index + 1}/{planned_trials} · 参数已更新",
        )

    if best_result is None:
        raise RuntimeError("Outdoor auto tuning produced no completed trial")
    report = {
        "status": "completed",
        "provider": "DeepSeek",
        "settings": settings.public_dict(),
        "navigation_key": config.outdoor_navigation_key,
        "best_trial": best_trial,
        "best_score": best_score,
        "trials": trial_records,
    }
    best_parameters_path = tuning_root / "best_parameters.json"
    best_parameters_path.write_text(
        json.dumps(
            _jsonable(trial_records[best_trial]["parameters"]),
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    report["best_parameters"] = trial_records[best_trial]["parameters"]
    report["best_parameters_path"] = str(best_parameters_path)
    report_path = tuning_root / "tuning_report.json"
    report_path.write_text(
        json.dumps(_jsonable(report), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    best_result["auto_tuning"] = report
    best_result["auto_tuning_report"] = str(report_path)
    best_result["auto_tuning_best_parameters"] = str(best_parameters_path)
    best_result["tuning_best_trial"] = int(best_trial)
    best_result["tuning_best_score"] = float(best_score)
    final_json = config.output_json or f"results/auto_tuned_outdoor_{config.outdoor_navigation_key}.json"
    best_result["output_json"] = _write_json(final_json, best_result)
    print("=== DEEPSEEK AUTO TUNING DONE ===")
    print(f"best_trial: {best_trial}")
    print(f"best_score: {best_score:.6f}")
    print(f"report: {report_path}")
    return best_result


def run_branch_simulation(config: BranchConfig):
    token = str(config.branch).strip().lower()
    if token == "uji":
        return run_uji_branch(config)
    if token == "own":
        return run_own_branch(config)
    if token == "outdoor":
        if config.outdoor_auto_tune:
            return run_outdoor_auto_tuning(config)
        return run_outdoor_branch(config)
    raise ValueError(f"Unsupported branch: {config.branch}. Use 'uji', 'own', or 'outdoor'.")
