"""Build a 2-D outdoor geomagnetic map from phone magnetometer and RTK data.

The RTK receiver keeps positions for longer than a single map survey.  This
module therefore derives the useful time window from the magnetic sessions and
uses only the GGA records that cover that window.  It intentionally contains no
navigation/test-route handling.
"""

from __future__ import annotations

import json
import math
import tomllib
import zipfile
from collections import Counter
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from pykrige.ok import OrdinaryKriging
from scipy.spatial import cKDTree


DEFAULT_CONFIG = {
    "data_root": "data/raw/outdoor_rtk_map",
    "output_dir": "data/processed/outdoor_rtk_map",
    "cell_size_m": 0.15,
    "grid_spacing_m": 0.10,
    "variogram_model": "exponential",
    "variogram_nlags": 12,
    "variogram_weight": True,
    "kriging_pseudo_inverse": True,
    "max_rtk_interpolation_gap_s": 0.5,
    "rtk_window_margin_s": 2.0,
    "required_fix_quality": [4],
    "color_percentiles": [1.0, 99.0],
}

MAG_COLUMNS = ["X (\u00b5T)", "Y (\u00b5T)", "Z (\u00b5T)"]
SECONDS_PER_DAY = 86400.0
TIMEZONE_OFFSET_HOURS = 8


def _load_config(config_path):
    cfg = dict(DEFAULT_CONFIG)
    path = Path(config_path)
    if path.exists():
        with path.open("rb") as handle:
            project_cfg = tomllib.load(handle)
        section = project_cfg.get("tool", {}).get("outdoor_map_builder", {})
        if isinstance(section, dict):
            cfg.update(section)
    return cfg


def _nmea_checksum_ok(sentence):
    if not sentence.startswith("$") or "*" not in sentence:
        return False
    body, checksum_text = sentence[1:].split("*", 1)
    try:
        expected = int(checksum_text[:2], 16)
    except (TypeError, ValueError):
        return False
    actual = 0
    for char in body:
        actual ^= ord(char)
    return actual == expected


def _parse_hhmmss(value):
    if len(value) < 6:
        raise ValueError("Incomplete NMEA time")
    return int(value[:2]) * 3600 + int(value[2:4]) * 60 + float(value[4:])


def _parse_lat_lon(fields):
    lat_raw, lat_hemi = fields[2], fields[3]
    lon_raw, lon_hemi = fields[4], fields[5]
    if not lat_raw or not lon_raw:
        raise ValueError("Missing GGA latitude or longitude")
    latitude = int(lat_raw[:2]) + float(lat_raw[2:]) / 60.0
    longitude = int(lon_raw[:3]) + float(lon_raw[3:]) / 60.0
    if lat_hemi == "S":
        latitude = -latitude
    elif lat_hemi != "N":
        raise ValueError("Invalid latitude hemisphere")
    if lon_hemi == "W":
        longitude = -longitude
    elif lon_hemi != "E":
        raise ValueError("Invalid longitude hemisphere")
    return latitude, longitude


def _load_rtk_gga(path):
    records = []
    bad_checksum = 0
    parse_errors = 0
    with Path(path).open("r", encoding="utf-8", errors="ignore") as handle:
        for line_number, line in enumerate(handle, 1):
            line = line.strip()
            if not line.startswith(("$GPGGA", "$GNGGA")):
                continue
            if not _nmea_checksum_ok(line):
                bad_checksum += 1
                continue
            fields = line.split("*", 1)[0].split(",")
            try:
                utc_seconds = _parse_hhmmss(fields[1])
                latitude, longitude = _parse_lat_lon(fields)
                records.append(
                    {
                        "seconds_of_day": (
                            utc_seconds + TIMEZONE_OFFSET_HOURS * 3600
                        )
                        % SECONDS_PER_DAY,
                        "latitude_deg": latitude,
                        "longitude_deg": longitude,
                        "fix_quality": int(fields[6] or 0),
                        "satellites": int(fields[7] or 0),
                        "hdop": float(fields[8]) if fields[8] else np.nan,
                        "altitude_m": float(fields[9]) if fields[9] else np.nan,
                        "source_line": line_number,
                    }
                )
            except (IndexError, TypeError, ValueError):
                parse_errors += 1
    if not records:
        raise ValueError(f"No checksum-valid GGA records in {path}")
    result = pd.DataFrame.from_records(records)
    result.attrs.update(bad_checksum=bad_checksum, parse_errors=parse_errors)
    return result


def _read_session_clock(path):
    with zipfile.ZipFile(path) as archive:
        mag = pd.read_csv(archive.open("Magnetometer.csv"))
        meta = pd.read_csv(archive.open("meta/time.csv"))
    missing = [name for name in ["Time (s)", *MAG_COLUMNS] if name not in mag]
    if missing:
        raise ValueError(f"{Path(path).name} is missing columns: {missing}")
    starts = meta.loc[meta["event"].astype(str).str.upper() == "START"]
    if starts.empty:
        raise ValueError(f"{Path(path).name}: meta/time.csv has no START row")
    start = starts.iloc[0]
    local_text = str(start["system time text"]).rsplit(" UTC", 1)[0]
    local_start = pd.Timestamp(local_text)
    experiment_start = float(start["experiment time"])
    sensor_time = pd.to_numeric(mag["Time (s)"], errors="raise").to_numpy(float)
    datetimes = local_start + pd.to_timedelta(sensor_time - experiment_start, unit="s")
    seconds = (
        datetimes.hour * 3600
        + datetimes.minute * 60
        + datetimes.second
        + datetimes.microsecond / 1_000_000.0
    ).to_numpy(float)
    return mag, datetimes, seconds


def _select_useful_rtk_window(rtk, survey_start_s, survey_end_s, margin_s):
    """Discard RTK history outside the actual magnetic-map acquisition window."""
    if survey_end_s < survey_start_s:
        raise ValueError("Outdoor map sessions crossing midnight are not supported yet")
    lower = survey_start_s - float(margin_s)
    upper = survey_end_s + float(margin_s)
    selected = rtk.loc[rtk["seconds_of_day"].between(lower, upper)].copy()
    selected = selected.sort_values(["seconds_of_day", "source_line"])
    selected = selected.drop_duplicates("seconds_of_day", keep="last").reset_index(drop=True)
    if selected.empty:
        raise ValueError(
            "No RTK records overlap the magnetic-session time range; check timezone and files"
        )
    if selected["seconds_of_day"].min() > survey_start_s or selected["seconds_of_day"].max() < survey_end_s:
        raise ValueError("RTK records do not fully cover the magnetic-session time range")
    return selected


def _add_local_enu(rtk, origin_lat, origin_lon):
    radius_m = 6371000.0
    lat = np.deg2rad(rtk["latitude_deg"].to_numpy(float))
    lon = np.deg2rad(rtk["longitude_deg"].to_numpy(float))
    lat0 = math.radians(origin_lat)
    lon0 = math.radians(origin_lon)
    result = rtk.copy()
    result["east_m"] = (lon - lon0) * radius_m * math.cos(lat0)
    result["north_m"] = (lat - lat0) * radius_m
    return result


def _interpolate_rtk(mag_seconds, rtk, max_gap_s):
    rt = rtk["seconds_of_day"].to_numpy(float)
    insert = np.searchsorted(rt, mag_seconds, side="left")
    left = np.clip(insert - 1, 0, len(rt) - 1)
    right = np.clip(insert, 0, len(rt) - 1)
    gaps = rt[right] - rt[left]
    inside = (insert > 0) & (insert < len(rt))
    valid = inside & (gaps > 0) & (gaps <= float(max_gap_s))
    nearest = np.where(
        np.abs(mag_seconds - rt[left]) <= np.abs(rt[right] - mag_seconds),
        left,
        right,
    )
    output = pd.DataFrame(
        {
            "latitude_deg": np.interp(mag_seconds, rt, rtk["latitude_deg"]),
            "longitude_deg": np.interp(mag_seconds, rt, rtk["longitude_deg"]),
            "east_m": np.interp(mag_seconds, rt, rtk["east_m"]),
            "north_m": np.interp(mag_seconds, rt, rtk["north_m"]),
            "rtk_fix_quality": rtk["fix_quality"].to_numpy(int)[nearest],
            "rtk_satellites": rtk["satellites"].to_numpy(int)[nearest],
            "rtk_hdop": rtk["hdop"].to_numpy(float)[nearest],
            "rtk_interp_gap_s": gaps,
            "rtk_valid": valid,
        }
    )
    output.loc[~valid, ["latitude_deg", "longitude_deg", "east_m", "north_m"]] = np.nan
    return output


def _process_session(path, rtk, cached_clock, max_gap_s):
    mag, datetimes, seconds = cached_clock
    aligned = _interpolate_rtk(seconds, rtk, max_gap_s=max_gap_s)
    xyz = mag[MAG_COLUMNS].apply(pd.to_numeric, errors="raise").to_numpy(float)
    magnitude = np.linalg.norm(xyz, axis=1)
    session_id = Path(path).stem
    samples = pd.DataFrame(
        {
            "session_id": session_id,
            "local_datetime": pd.DatetimeIndex(datetimes).strftime("%Y-%m-%d %H:%M:%S.%f"),
            "seconds_of_day": seconds,
            "sensor_time_s": pd.to_numeric(mag["Time (s)"], errors="raise"),
            "x_uT": xyz[:, 0],
            "y_uT": xyz[:, 1],
            "z_uT": xyz[:, 2],
            "b_total_uT": magnitude,
            "latitude_deg": aligned["latitude_deg"],
            "longitude_deg": aligned["longitude_deg"],
            "east_m": aligned["east_m"],
            "north_m": aligned["north_m"],
            "rtk_fix_quality": aligned["rtk_fix_quality"],
            "rtk_satellites": aligned["rtk_satellites"],
            "rtk_hdop": aligned["rtk_hdop"],
            "rtk_interp_gap_s": aligned["rtk_interp_gap_s"],
            "rtk_valid": aligned["rtk_valid"],
            "source_magnetic_zip": Path(path).name,
            "source_rtk_log": "LOG00040.TXT",
            "time_source": "meta/time.csv START system time",
        }
    )
    valid = samples["rtk_valid"]
    quality_counts = Counter(samples.loc[valid, "rtk_fix_quality"].astype(int))
    summary = {
        "session_id": session_id,
        "samples": int(len(samples)),
        "corrected_start": samples["local_datetime"].iloc[0],
        "corrected_end": samples["local_datetime"].iloc[-1],
        "b_median_uT": float(np.median(magnitude)),
        "b_p01_uT": float(np.quantile(magnitude, 0.01)),
        "b_p99_uT": float(np.quantile(magnitude, 0.99)),
        "rtk_valid_samples": int(valid.sum()),
        "rtk_q4_samples": int(quality_counts[4]),
        "rtk_q4_percent": 100.0 * quality_counts[4] / max(int(valid.sum()), 1),
        "review_flag": "",
    }
    return samples, summary


def _spatial_bin(samples, cell_size_m):
    data = samples.copy()
    east0 = float(data["east_m"].min())
    north0 = float(data["north_m"].min())
    data["east_cell"] = np.floor((data["east_m"] - east0) / cell_size_m).astype(int)
    data["north_cell"] = np.floor((data["north_m"] - north0) / cell_size_m).astype(int)
    grouped = data.groupby(["east_cell", "north_cell"], sort=True)
    cells = grouped.agg(
        east_m=("east_m", "median"),
        north_m=("north_m", "median"),
        b_total_uT=("b_total_uT", "median"),
        b_mean_uT=("b_total_uT", "mean"),
        sample_count=("b_total_uT", "size"),
        session_count=("session_id", "nunique"),
    ).reset_index()
    quartiles = grouped["b_total_uT"].quantile([0.25, 0.75]).unstack()
    quartiles.columns = ["b_q25_uT", "b_q75_uT"]
    cells = cells.merge(quartiles.reset_index(), on=["east_cell", "north_cell"])
    cells["b_iqr_uT"] = cells["b_q75_uT"] - cells["b_q25_uT"]
    return cells


def _ordinary_kriging(cells, cfg):
    x = cells["east_m"].to_numpy(float)
    y = cells["north_m"].to_numpy(float)
    z = cells["b_total_uT"].to_numpy(float)
    spacing = float(cfg["grid_spacing_m"])
    grid_x = np.arange(x.min(), x.max() + spacing * 0.5, spacing)
    grid_y = np.arange(y.min(), y.max() + spacing * 0.5, spacing)
    model = OrdinaryKriging(
        x,
        y,
        z,
        variogram_model=str(cfg["variogram_model"]),
        nlags=int(cfg["variogram_nlags"]),
        weight=bool(cfg["variogram_weight"]),
        coordinates_type="euclidean",
        pseudo_inv=bool(cfg["kriging_pseudo_inverse"]),
        verbose=False,
        enable_plotting=False,
    )
    estimate, variance = model.execute("grid", grid_x, grid_y)
    grid_z = np.asarray(np.ma.filled(estimate, np.nan), dtype=float)
    variance = np.asarray(np.ma.filled(variance, np.nan), dtype=float)
    if not np.isfinite(grid_z).all():
        raise RuntimeError("Ordinary Kriging did not fill the complete rectangular map")
    xx, yy = np.meshgrid(grid_x, grid_y)
    nearest, _ = cKDTree(np.column_stack([x, y])).query(
        np.column_stack([xx.ravel(), yy.ravel()]), k=1
    )
    return grid_x, grid_y, grid_z, variance, nearest.reshape(grid_z.shape)


def _plot_map(grid_x, grid_y, grid_z, cells, output_png, percentiles):
    low, high = np.nanpercentile(cells["b_total_uT"].to_numpy(float), percentiles)
    xx, yy = np.meshgrid(grid_x, grid_y)
    fig, ax = plt.subplots(figsize=(9, 8), constrained_layout=True)
    levels = np.linspace(float(low), float(high), 31)
    contour = ax.contourf(xx, yy, grid_z, levels=levels, cmap="turbo", extend="both")
    ax.scatter(cells["east_m"], cells["north_m"], s=2, c="black", alpha=0.18)
    fig.colorbar(contour, ax=ax, label="Total magnetic field (\u00b5T)")
    ax.set(title="Outdoor geomagnetic map - Ordinary Kriging", xlabel="Local East (m)", ylabel="Local North (m)")
    ax.set_aspect("equal", adjustable="box")
    ax.grid(alpha=0.15)
    fig.savefig(output_png, dpi=240)
    plt.close(fig)


def _map_info(model_path, metadata_path):
    metadata = json.loads(Path(metadata_path).read_text(encoding="utf-8"))
    with np.load(model_path) as model:
        points = np.column_stack([model["x_train"], model["y_train"], model["z_train"]])
        grid = np.asarray(model["grid_magnitude"], dtype=float)
        grid_x = np.asarray(model["grid_x"], dtype=float)
        grid_y = np.asarray(model["grid_y"], dtype=float)
    cell_size = float(metadata["cell_size_m"])
    grid_spacing = float(metadata["grid_spacing_m"])
    return {
        **metadata,
        "source": "outdoor",
        "status": "ready",
        "continuous_map": True,
        "point_cloud_mode": "point_cloud",
        "point_cloud_shape": [int(points.shape[0]), 3],
        "map_points": points.tolist(),
        "grid_array": grid.tolist(),
        "map": grid.tolist(),
        "rangex_min": float(grid_x.min()),
        "rangex_max": float(grid_x.max()),
        "rangey_min": float(grid_y.min()),
        "rangey_max": float(grid_y.max()),
        "grid_map_contract": {
            "selected_format": "npz_matrix",
            "path": str(model_path),
            "meta": {
                "cell_size_m": grid_spacing,
                "origin_xy_m": [float(grid_x.min()), float(grid_y.min())],
                "x_axis_direction": "east",
                "y_axis_direction": "north",
                "flip_y": False,
                "mag_unit": "uT",
                "spatial_bin_size_m": cell_size,
            },
        },
    }


def build_outdoor_rtk_map(
    data_root=None,
    output_dir=None,
    config_path="pyproject.toml",
    force_rebuild=False,
):
    """Build or load the outdoor RTK geomagnetic map and return map API data."""
    cfg = _load_config(config_path)
    data_root = Path(data_root or cfg["data_root"])
    output_dir = Path(output_dir or cfg["output_dir"])
    magnetic_dir = data_root / "magnetic_sessions"
    rtk_path = data_root / "rtk" / "LOG00040.TXT"
    model_path = output_dir / "outdoor_magnetic_map_kriging.npz"
    metadata_path = output_dir / "outdoor_magnetic_map_meta.json"
    preview_path = output_dir / "outdoor_magnetic_map_kriging.png"
    variance_path = output_dir / "outdoor_kriging_variance.png"
    cells_path = output_dir / "outdoor_map_cells.csv"
    samples_path = output_dir / "outdoor_map_samples_corrected.csv"
    summary_path = output_dir / "outdoor_session_summary.csv"

    if not force_rebuild and model_path.exists() and metadata_path.exists():
        return _map_info(model_path, metadata_path)
    if not rtk_path.exists():
        raise FileNotFoundError(f"Outdoor map RTK log not found: {rtk_path}")
    session_paths = sorted(magnetic_dir.glob("*Geomagnetic Map Building*.zip"))
    if not session_paths:
        raise FileNotFoundError(f"No outdoor map-building sessions found in {magnetic_dir}")

    clocks = {path: _read_session_clock(path) for path in session_paths}
    survey_start = min(float(clock[2].min()) for clock in clocks.values())
    survey_end = max(float(clock[2].max()) for clock in clocks.values())
    raw_rtk = _load_rtk_gga(rtk_path)
    rtk = _select_useful_rtk_window(
        raw_rtk,
        survey_start,
        survey_end,
        margin_s=float(cfg["rtk_window_margin_s"]),
    )
    required_quality = {int(value) for value in cfg["required_fix_quality"]}
    origin_rows = rtk.loc[rtk["fix_quality"].isin(required_quality)]
    if origin_rows.empty:
        raise ValueError("No RTK records satisfy required_fix_quality in the survey window")
    origin_lat = float(origin_rows["latitude_deg"].median())
    origin_lon = float(origin_rows["longitude_deg"].median())
    rtk = _add_local_enu(rtk, origin_lat, origin_lon)

    session_samples = []
    summaries = []
    for path in session_paths:
        samples, summary = _process_session(
            path,
            rtk,
            clocks[path],
            max_gap_s=float(cfg["max_rtk_interpolation_gap_s"]),
        )
        session_samples.append(samples)
        summaries.append(summary)
    samples = pd.concat(session_samples, ignore_index=True).sort_values("seconds_of_day")
    summary = pd.DataFrame(summaries).sort_values("corrected_start").reset_index(drop=True)

    medians = summary["b_median_uT"].to_numpy(float)
    center = float(np.median(medians))
    mad = float(np.median(np.abs(medians - center)))
    if mad > 0:
        review = np.abs(summary["b_median_uT"] - center) > 4.0 * mad
        summary.loc[review, "review_flag"] = "review_magnitude_shift_do_not_auto_exclude"
    samples["session_review_flag"] = samples["session_id"].map(
        summary.set_index("session_id")["review_flag"]
    ).fillna("")

    usable = samples.loc[
        samples["rtk_valid"] & samples["rtk_fix_quality"].isin(required_quality)
    ].copy()
    if usable.empty:
        raise RuntimeError("No map samples remain after RTK time and quality filtering")
    cells = _spatial_bin(usable, float(cfg["cell_size_m"]))
    grid_x, grid_y, grid_z, variance, nearest = _ordinary_kriging(cells, cfg)

    output_dir.mkdir(parents=True, exist_ok=True)
    samples.to_csv(samples_path, index=False, float_format="%.9f")
    summary.to_csv(summary_path, index=False, float_format="%.6f")
    cells.to_csv(cells_path, index=False, float_format="%.9f")
    np.savez_compressed(
        model_path,
        mode=np.array(["outdoor_rtk_ordinary_kriging"]),
        x_train=cells["east_m"].to_numpy(float),
        y_train=cells["north_m"].to_numpy(float),
        z_train=cells["b_total_uT"].to_numpy(float),
        grid_x=grid_x,
        grid_y=grid_y,
        grid_magnitude=grid_z,
        grid_variance=variance,
        nearest_sample_distance_m=nearest,
        origin_lat=np.array([origin_lat]),
        origin_lon=np.array([origin_lon]),
    )
    _plot_map(grid_x, grid_y, grid_z, cells, preview_path, cfg["color_percentiles"])
    xx, yy = np.meshgrid(grid_x, grid_y)
    fig, ax = plt.subplots(figsize=(9, 8), constrained_layout=True)
    mesh = ax.pcolormesh(xx, yy, np.maximum(variance, 0.0), shading="auto", cmap="magma")
    fig.colorbar(mesh, ax=ax, label="Kriging variance (\u00b5T\u00b2)")
    ax.set(title="Outdoor Ordinary Kriging variance", xlabel="Local East (m)", ylabel="Local North (m)")
    ax.set_aspect("equal", adjustable="box")
    fig.savefig(variance_path, dpi=220)
    plt.close(fig)

    metadata = {
        "source": "outdoor",
        "continuous_map": True,
        "coordinate_system": "local ENU equirectangular approximation",
        "origin_latlon": [origin_lat, origin_lon],
        "time_method": "meta/time.csv START system time + sensor experiment time",
        "rtk_timezone": "NMEA UTC converted to UTC+08:00",
        "rtk_selection": "only GGA records overlapping the magnetic survey time window",
        "rtk_source_records": int(len(raw_rtk)),
        "rtk_selected_records": int(len(rtk)),
        "rtk_selected_source_lines": [int(rtk["source_line"].min()), int(rtk["source_line"].max())],
        "survey_time_seconds_of_day": [survey_start, survey_end],
        "map_sessions": int(len(session_paths)),
        "map_input_samples": int(len(samples)),
        "map_usable_samples": int(len(usable)),
        "spatial_cells": int(len(cells)),
        "cell_size_m": float(cfg["cell_size_m"]),
        "grid_spacing_m": float(cfg["grid_spacing_m"]),
        "grid_shape": [int(grid_z.shape[0]), int(grid_z.shape[1])],
        "filled_grid_cells": int(np.isfinite(grid_z).sum()),
        "total_grid_cells": int(grid_z.size),
        "interpolation_method": "PyKrige OrdinaryKriging",
        "variogram_model": str(cfg["variogram_model"]),
        "variogram_nlags": int(cfg["variogram_nlags"]),
        "variogram_weight": bool(cfg["variogram_weight"]),
        "kriging_pseudo_inverse": bool(cfg["kriging_pseudo_inverse"]),
        "output_model_npz": str(model_path),
        "output_preview_npz": str(model_path),
        "output_json": str(metadata_path),
        "output_png": str(preview_path),
        "output_variance_png": str(variance_path),
        "output_cells_csv": str(cells_path),
        "output_samples_csv": str(samples_path),
        "output_session_summary_csv": str(summary_path),
    }
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    return _map_info(model_path, metadata_path)

