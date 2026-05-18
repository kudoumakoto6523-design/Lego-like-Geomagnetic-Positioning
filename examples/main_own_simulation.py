import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np

from Geomag import PFConfig, PDRConfig, build_pdr_from_config, build_pf_from_config
from Geomag.algorithms import get_map, get_sensor, get_test_len, get_true_route
from Geomag.models import PFState
from Geomag.own_dataset_registry import get_own_dataset_spec
from Geomag.pipeline import GeomagPipeline
from data.own_data.magnetometer_map_own import data as own_map_raw


def build_main_configs():
    # Keep this aligned with main.py.
    pdr_config = PDRConfig(
        step_judge="peak_dynamic",
        step_judge_params={"peak_sigma": 0.4, "peak_prominence": 0.2, "min_samples_per_step": 4.5},
        step_length="weinberg",
        step_length_params={"weinberg_k": 0.45},
        heading="gyro",
        heading_params={"dt": 0.01},
        mag="norm_mean",
    )
    pf_config = PFConfig(
        state_params={"num_particles": 5000, "min_particles": 2000, "max_particles": 10000000000000},
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


def build_own_tile_matrix(raw_matrix, mode="raw", rows=8, cols=12):
    arr = np.asarray(raw_matrix, dtype=float)
    if arr.ndim != 2 or arr.shape[0] == 0 or arr.shape[1] == 0:
        raise ValueError("magnetometer_map_own.data must be a non-empty 2D matrix.")
    token = str(mode).strip().lower()
    if token == "raw":
        return arr
    if token not in {"tile12", "tile_12"}:
        raise ValueError(f"Unsupported map mode: {mode}. Use 'raw' or 'tile12'.")
    ridx = np.linspace(0, arr.shape[0] - 1, rows, dtype=int)
    cidx = np.linspace(0, arr.shape[1] - 1, cols, dtype=int)
    return arr[ridx][:, cidx]


def _polyline_points(route_xy):
    arr = np.asarray(route_xy, dtype=float)
    if arr.ndim != 2 or arr.shape[0] < 2 or arr.shape[1] < 2:
        raise ValueError("route_xy must be shape (N,2) with N>=2.")
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


def route_point_at_fraction(route_xy, frac):
    seg = sample_route_segment(route_xy, start_frac=float(frac), end_frac=min(1.0, float(frac) + 1e-6), n=2)
    return [float(seg[0, 0]), float(seg[0, 1])]


def corrected_heading(heading_angle, mirror_y=True, heading_offset_deg=0.0):
    ang = float(heading_angle) + math.radians(float(heading_offset_deg))
    if mirror_y:
        ang = -ang
    return float(((ang + math.pi) % (2.0 * math.pi)) - math.pi)


def summarize_error(track, route, geomag_map):
    route_x, route_y = GeomagPipeline._route_to_xy_for_error(route, geomag_map)
    if route_x is None or route_y is None:
        return None, None
    series = GeomagPipeline._compute_error_series(track, route_x, route_y)
    stats = GeomagPipeline._summarize_error(series)
    return series, stats


def save_trajectory_plot(geomag_map, route, pdr_list, pf_list, output_png, show=False):
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

    rr = np.asarray(route, dtype=float)
    ppdr = np.asarray(pdr_list, dtype=float)
    ppf = np.asarray(pf_list, dtype=float)

    if rr.ndim == 2 and rr.shape[1] >= 2:
        ax.plot(rr[:, 0], rr[:, 1], "w-", linewidth=2.0, label="route")
        ax.scatter([rr[0, 0]], [rr[0, 1]], c="lime", s=30, zorder=3, label="route_start")
    if ppdr.ndim == 2 and ppdr.shape[1] >= 2:
        ax.plot(ppdr[:, 0], ppdr[:, 1], "--", color="orange", linewidth=1.5, label="pdr")
    if ppf.ndim == 2 and ppf.shape[1] >= 2:
        ax.plot(ppf[:, 0], ppf[:, 1], "-", color="cyan", linewidth=1.8, label="pf")

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


def run_own_simulation(
    own_dataset_key="route1_run1",
    window_size=400,
    max_frames=None,
    map_mode="raw",
    mirror_y=True,
    heading_offset_deg=0.0,
    trim_head=0,
    trim_tail=0,
    output_json=None,
    output_png=None,
    show=False,
):
    dataset_spec = get_own_dataset_spec(own_dataset_key)
    tile_matrix = build_own_tile_matrix(own_map_raw, mode=map_mode, rows=8, cols=12)
    rows, cols = tile_matrix.shape
    if str(map_mode).strip().lower() == "raw":
        tile_size_x_m = 11.52 / float(cols)
        tile_size_y_m = 8.80 / float(rows)
    else:
        tile_size_x_m = 0.96
        tile_size_y_m = 1.10

    geomag_map = get_map(
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

    route_full = get_true_route(source="own", own_dataset_key=own_dataset_key)
    if not route_full:
        raise ValueError("Route is empty.")

    pdr_config, pf_config = build_main_configs()
    pdr_module = build_pdr_from_config(pdr_config)
    pf_module = build_pf_from_config(pf_config)
    full_frames = int(get_test_len(source="own", own_dataset_key=own_dataset_key))
    head = max(0, int(trim_head))
    tail = max(0, int(trim_tail))
    usable_frames = full_frames - head - tail
    if usable_frames <= 1:
        raise ValueError(
            f"Invalid trim config: total={full_frames}, trim_head={head}, trim_tail={tail}. No frames left."
        )
    total_frames = usable_frames if max_frames is None else min(usable_frames, int(max_frames))

    start_frac = head / float(full_frames)
    end_frac = (head + total_frames) / float(full_frames)
    route = sample_route_segment(route_full, start_frac=start_frac, end_frac=end_frac, n=max(100, total_frames // 2))

    init_xy = [float(route[0, 0]), float(route[0, 1])]
    pf_state = PFState(init_pos=init_xy, mag_map=geomag_map, **dict(pf_config.state_params or {}))

    pf_list = [pf_state.get_pos()]
    pdr_list = [pf_state.get_pos()]
    geomag_hist = []
    sample_buffer = []

    for _ in range(head):
        get_sensor(source="own", own_dataset_key=own_dataset_key)

    def _print_progress(current, total, width=36):
        total = max(int(total), 1)
        current = min(max(int(current), 0), total)
        ratio = current / total
        filled = int(width * ratio)
        bar = "#" * filled + "-" * (width - filled)
        sys.stdout.write(f"\rProgress [{bar}] {current}/{total} ({ratio * 100:5.1f}%)")
        sys.stdout.flush()

    _print_progress(0, total_frames)
    for frame_idx in range(total_frames):
        mag, acc, gyro = get_sensor(source="own", own_dataset_key=own_dataset_key)
        sample_buffer.append([acc, gyro, mag])
        if not pdr_module.detect_step(sample_buffer):
            _print_progress(frame_idx + 1, total_frames)
            continue

        step_len = float(pdr_module.estimate_step_len(sample_buffer))
        heading_raw = float(pdr_module.estimate_heading(sample_buffer))
        heading_angle = corrected_heading(heading_raw, mirror_y=bool(mirror_y), heading_offset_deg=heading_offset_deg)
        obs_mag = float(pdr_module.extract_mag())
        geomag_hist.append(obs_mag)
        geomag_window = geomag_hist[-window_size:]

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
        sample_buffer.clear()
        _print_progress(frame_idx + 1, total_frames)

    sys.stdout.write("\n")
    sys.stdout.flush()

    pdr_series, pdr_stats = summarize_error(pdr_list, route, geomag_map)
    pf_series, pf_stats = summarize_error(pf_list, route, geomag_map)

    if output_json is None:
        output_json = f"results/own_simulation_{own_dataset_key}.json"
    if output_png is None:
        output_png = f"results/own_simulation_{own_dataset_key}.png"

    plot_path = save_trajectory_plot(
        geomag_map=geomag_map,
        route=route,
        pdr_list=pdr_list,
        pf_list=pf_list,
        output_png=output_png,
        show=show,
    )

    payload = {
        "dataset_key": own_dataset_key,
        "dataset_dir": dataset_spec["dataset_dir"],
        "route_label": dataset_spec["route_label"],
        "map_mode": str(map_mode),
        "mirror_y": bool(mirror_y),
        "heading_offset_deg": float(heading_offset_deg),
        "trim_head": int(head),
        "trim_tail": int(tail),
        "full_sensor_frames": int(full_frames),
        "map_point_cloud_mode": geomag_map.get("point_cloud_mode"),
        "map_point_cloud_shape": geomag_map.get("point_cloud_shape"),
        "map_bounds": [
            geomag_map.get("rangex_min"),
            geomag_map.get("rangex_max"),
            geomag_map.get("rangey_min"),
            geomag_map.get("rangey_max"),
        ],
        "route_len": int(len(route)),
        "sensor_frames_used": int(total_frames),
        "steps_detected": max(0, len(pf_list) - 1),
        "pdr_error_stats": pdr_stats,
        "pf_error_stats": pf_stats,
        "pdr_error_series": None if pdr_series is None else np.asarray(pdr_series, dtype=float).tolist(),
        "pf_error_series": None if pf_series is None else np.asarray(pf_series, dtype=float).tolist(),
        "pdr_track": [list(map(float, xy)) for xy in pdr_list],
        "pf_track": [list(map(float, xy)) for xy in pf_list],
        "route_xy_m": [list(map(float, xy)) for xy in route],
        "plot_png": plot_path,
    }

    out_json = Path(output_json)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    print("=== OWN SIMULATION DONE ===")
    print(f"dataset_key: {own_dataset_key}")
    print(f"dataset_dir: {dataset_spec['dataset_dir']}")
    print(f"route_label: {dataset_spec['route_label']}")
    print(f"map_mode: {map_mode}")
    print(f"mirror_y: {bool(mirror_y)}")
    print(f"trim_head/tail: {head}/{tail}")
    print(f"sensor_frames_used: {total_frames}")
    print(f"steps_detected: {max(0, len(pf_list) - 1)}")
    print(f"pf_error_stats: {pf_stats}")
    print(f"pdr_error_stats: {pdr_stats}")
    print(f"saved_json: {out_json}")
    if plot_path:
        print(f"saved_plot: {plot_path}")

    return payload


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run own-data PF/PDR simulation (main.py config) on normalized package.")
    parser.add_argument("--dataset-key", type=str, default="route1_run1", help="Own dataset key (e.g. route1_run1).")
    parser.add_argument("--window-size", type=int, default=400, help="Geomagnetic history window.")
    parser.add_argument("--max-frames", type=int, default=None, help="Limit frames for quick debug.")
    parser.add_argument("--map-mode", type=str, default="raw", choices=["raw", "tile12"], help="Map mode from magnetometer_map_own data.")
    parser.add_argument("--mirror-y", action="store_true", help="Mirror heading in Y (fix mirrored PDR direction).")
    parser.add_argument("--no-mirror-y", dest="mirror_y", action="store_false", help="Disable Y mirror correction.")
    parser.set_defaults(mirror_y=True)
    parser.add_argument("--heading-offset-deg", type=float, default=0.0, help="Heading offset in degrees after mirror correction.")
    parser.add_argument("--trim-head", type=int, default=0, help="Drop first N sensor frames.")
    parser.add_argument("--trim-tail", type=int, default=0, help="Drop last N sensor frames.")
    parser.add_argument("--output-json", type=str, default=None, help="Output JSON path.")
    parser.add_argument("--output-png", type=str, default=None, help="Output trajectory PNG path.")
    parser.add_argument("--show", action="store_true", help="Show matplotlib figure.")
    args = parser.parse_args()

    run_own_simulation(
        own_dataset_key=args.dataset_key,
        window_size=args.window_size,
        max_frames=args.max_frames,
        map_mode=args.map_mode,
        mirror_y=bool(args.mirror_y),
        heading_offset_deg=args.heading_offset_deg,
        trim_head=args.trim_head,
        trim_tail=args.trim_tail,
        output_json=args.output_json,
        output_png=args.output_png,
        show=bool(args.show),
    )
