"""Outdoor navigation/test data adapter.

Each navigation archive supplies accelerometer, gyroscope and magnetometer
samples.  LOG00042.TXT supplies the matching RTK ground truth.  Both streams
are placed on the accelerometer time axis and only checksum-valid, quality-4
RTK positions are retained.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

from Geomag.outdoor_mapping import (
    MAG_COLUMNS,
    _add_local_enu,
    _interpolate_rtk,
    _load_rtk_gga,
    _select_useful_rtk_window,
)


DEFAULT_OUTDOOR_DATA_ROOT = "data/raw/outdoor_rtk_map"
NAVIGATION_ARCHIVES = {
    "nav1": "Geomagnetic Navigation 2026-04-29 22-18-43.zip",
    "nav2": "Geomagnetic Navigation 2026-04-29 22-19-50.zip",
    "nav3": "Geomagnetic Navigation 2026-04-29 22-22-00.zip",
    "nav4": "Geomagnetic Navigation 2026-04-29 22-22-52.zip",
    "nav5": "Geomagnetic Navigation 2026-04-29 22-23-36.zip",
}


def available_outdoor_navigation_keys():
    return list(NAVIGATION_ARCHIVES)


def resolve_outdoor_navigation(selection="nav1", data_root=DEFAULT_OUTDOOR_DATA_ROOT):
    token = str(selection or "nav1").strip()
    if not token:
        token = "nav1"
    if token.isdigit():
        token = f"nav{int(token)}"
    token_lower = token.lower()
    if token_lower in NAVIGATION_ARCHIVES:
        archive_path = Path(data_root) / "navigation_sessions" / NAVIGATION_ARCHIVES[token_lower]
        key = token_lower
    else:
        candidate = Path(token)
        if candidate.suffix.lower() != ".zip":
            candidate = Path(data_root) / "navigation_sessions" / token
        elif not candidate.is_absolute() and not candidate.exists():
            candidate = Path(data_root) / "navigation_sessions" / candidate.name
        archive_path = candidate
        key = next(
            (name for name, filename in NAVIGATION_ARCHIVES.items() if filename == archive_path.name),
            archive_path.stem,
        )
    return {
        "outdoor_navigation_key": key,
        "outdoor_navigation_zip": str(archive_path),
        "outdoor_data_root": str(data_root),
    }


def _start_clock(meta):
    starts = meta.loc[meta["event"].astype(str).str.upper() == "START"]
    if starts.empty:
        raise ValueError("meta/time.csv does not contain a START row")
    row = starts.iloc[0]
    local_text = str(row["system time text"]).rsplit(" UTC", 1)[0]
    return pd.Timestamp(local_text), float(row["experiment time"])


def _numeric_xyz(frame, columns, archive_name):
    missing = [column for column in ["Time (s)", *columns] if column not in frame]
    if missing:
        raise ValueError(f"{archive_name} is missing columns: {missing}")
    times = pd.to_numeric(frame["Time (s)"], errors="raise").to_numpy(float)
    values = frame[columns].apply(pd.to_numeric, errors="raise").to_numpy(float)
    order = np.argsort(times)
    times = times[order]
    values = values[order]
    times, unique_index = np.unique(times, return_index=True)
    return times, values[unique_index]


def load_outdoor_navigation_session(
    selection="nav1",
    data_root=DEFAULT_OUTDOOR_DATA_ROOT,
    max_rtk_gap_s=0.5,
    required_fix_quality=(4,),
):
    """Return synchronized sensor frames and RTK ground-truth route."""
    resolved = resolve_outdoor_navigation(selection, data_root=data_root)
    archive_path = Path(resolved["outdoor_navigation_zip"])
    rtk_path = Path(data_root) / "rtk" / "LOG00042.TXT"
    if not archive_path.exists():
        raise FileNotFoundError(f"Outdoor navigation archive not found: {archive_path}")
    if not rtk_path.exists():
        raise FileNotFoundError(f"Outdoor navigation RTK log not found: {rtk_path}")

    with zipfile.ZipFile(archive_path) as archive:
        accelerometer = pd.read_csv(archive.open("Accelerometer.csv"))
        gyroscope = pd.read_csv(archive.open("Gyroscope.csv"))
        magnetometer = pd.read_csv(archive.open("Magnetometer.csv"))
        meta = pd.read_csv(archive.open("meta/time.csv"))

    acc_t, acc = _numeric_xyz(
        accelerometer,
        ["X (m/s^2)", "Y (m/s^2)", "Z (m/s^2)"],
        archive_path.name,
    )
    gyro_t, gyro = _numeric_xyz(
        gyroscope,
        ["X (rad/s)", "Y (rad/s)", "Z (rad/s)"],
        archive_path.name,
    )
    mag_t, mag = _numeric_xyz(magnetometer, MAG_COLUMNS, archive_path.name)

    # PDR is acceleration-driven, so all channels and RTK truth use this axis.
    gyro_aligned = np.column_stack(
        [np.interp(acc_t, gyro_t, gyro[:, axis]) for axis in range(3)]
    )
    mag_aligned = np.column_stack(
        [np.interp(acc_t, mag_t, mag[:, axis]) for axis in range(3)]
    )
    local_start, experiment_start = _start_clock(meta)
    local_datetimes = local_start + pd.to_timedelta(acc_t - experiment_start, unit="s")
    seconds_of_day = (
        local_datetimes.hour * 3600
        + local_datetimes.minute * 60
        + local_datetimes.second
        + local_datetimes.microsecond / 1_000_000.0
    ).to_numpy(float)

    raw_rtk = _load_rtk_gga(rtk_path)
    rtk = _select_useful_rtk_window(
        raw_rtk,
        float(seconds_of_day.min()),
        float(seconds_of_day.max()),
        margin_s=2.0,
    )
    required_quality = {int(value) for value in required_fix_quality}
    origin_rows = rtk.loc[rtk["fix_quality"].isin(required_quality)]
    if origin_rows.empty:
        raise ValueError(f"{resolved['outdoor_navigation_key']}: no required-quality RTK rows")
    rtk = _add_local_enu(
        rtk,
        float(origin_rows["latitude_deg"].median()),
        float(origin_rows["longitude_deg"].median()),
    )
    truth = _interpolate_rtk(seconds_of_day, rtk, max_gap_s=max_rtk_gap_s)
    usable = truth["rtk_valid"].to_numpy(bool) & truth["rtk_fix_quality"].isin(required_quality).to_numpy(bool)
    usable &= np.isfinite(acc).all(axis=1)
    usable &= np.isfinite(gyro_aligned).all(axis=1)
    usable &= np.isfinite(mag_aligned).all(axis=1)
    if not np.any(usable):
        raise RuntimeError(
            f"{resolved['outdoor_navigation_key']}: no synchronized quality-4 sensor/RTK frames"
        )

    frame_indices = np.flatnonzero(usable)
    frames = []
    route = []
    for index in frame_indices:
        latitude = float(truth.iloc[index]["latitude_deg"])
        longitude = float(truth.iloc[index]["longitude_deg"])
        frames.append(
            {
                "time": float(seconds_of_day[index]),
                "sensor_time_s": float(acc_t[index]),
                "mag": [float(value) for value in mag_aligned[index]],
                "acc": [float(value) for value in acc[index]],
                "gyro": [float(value) for value in gyro_aligned[index]],
                "gyro_mode": "angular_rate_rad_s",
                "source": "outdoor",
                "ground_truth_latlon": [latitude, longitude],
                "rtk_fix_quality": int(truth.iloc[index]["rtk_fix_quality"]),
            }
        )
        route.append([latitude, longitude])

    return {
        **resolved,
        "frames": frames,
        "route_latlon": route,
        "sensor_frames_raw": int(acc_t.size),
        "sensor_frames_synchronized": int(len(frames)),
        "rtk_source_records": int(len(raw_rtk)),
        "rtk_window_records": int(len(rtk)),
        "required_fix_quality": sorted(required_quality),
        "start_local": str(local_datetimes[frame_indices[0]]),
        "end_local": str(local_datetimes[frame_indices[-1]]),
    }

