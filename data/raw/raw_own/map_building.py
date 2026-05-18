import zipfile
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

from gstools import Gaussian
from pykrige.ok import OrdinaryKriging


# =========================
# 0. 参数区：主要改这里
# =========================

ZIP_DIR = Path("../../own_data/")          # zip 文件所在文件夹，按你的实际路径改
TARGET_N_LINES = 8            # 你的有效测线数量。如果想全部使用，改成 None

LINE_GAP = 0.96               # 相邻测线间距，单位 m
LINE_LENGTH = 8 * 1.01        # 每条测线长度，单位 m，也就是 8.08 m

FIXED_TRIM_SEC = 0.20         # 起点和终点固定剔除时间，单位 s
AUTO_STATIC_TRIM = True       # 是否根据末端磁场变化率进一步判断停顿段

TARGET_DY = 0.05              # 沿线方向降采样到 5 cm 一个点
GRID_RESOLUTION = 0.02        # 输出地图网格分辨率，2 cm

LEN_X = 1.20                  # x 方向相关长度，跨测线方向
LEN_Y = 0.25                  # y 方向相关长度，沿测线方向
NUGGET_RATIO = 0.02           # 噪声项比例

OUTPUT_NPZ = "own_geomagnetic_map_kriging_from_zip.npz"
OUTPUT_PNG = "own_geomagnetic_map_kriging_from_zip.png"


# =========================
# 1. 读取 zip 文件
# =========================

def get_start_time_from_zip(zip_path):
    """
    用 meta/time.csv 里的 START 时间排序。
    如果读不到，就退回用文件名排序。
    """
    try:
        with zipfile.ZipFile(zip_path, "r") as z:
            with z.open("meta/time.csv") as f:
                meta_time = pd.read_csv(f)

        start_row = meta_time[meta_time["event"] == "START"]
        if len(start_row) > 0:
            return float(start_row["system time"].iloc[0])
    except Exception:
        pass

    return None


def find_abs_field_column(df):
    """
    自动寻找绝对磁场列。
    你的数据列名一般是 Absolute field (µT)。
    """
    for col in df.columns:
        name = col.lower()
        if "absolute" in name and "field" in name:
            return col

    raise ValueError(f"没有找到 Absolute field 列。当前列名为: {list(df.columns)}")


def find_time_column(df):
    """
    自动寻找时间列。
    你的数据列名一般是 Time (s)。
    """
    for col in df.columns:
        name = col.lower()
        if "time" in name:
            return col

    raise ValueError(f"没有找到 Time 列。当前列名为: {list(df.columns)}")


def read_one_magnetometer_zip(zip_path):
    """
    读取一个 Magnetometer zip 文件，返回 time 和 absolute magnetic field。
    """
    with zipfile.ZipFile(zip_path, "r") as z:
        names = z.namelist()

        raw_candidates = [name for name in names if name.endswith("Raw Data.csv")]
        if len(raw_candidates) == 0:
            raise ValueError(f"{zip_path.name} 中没有找到 Raw Data.csv")

        raw_name = raw_candidates[0]

        with z.open(raw_name) as f:
            df = pd.read_csv(f)

    time_col = find_time_column(df)
    abs_col = find_abs_field_column(df)

    t = df[time_col].to_numpy(dtype=float)
    b = df[abs_col].to_numpy(dtype=float)

    valid = np.isfinite(t) & np.isfinite(b)
    t = t[valid]
    b = b[valid]

    return t, b


def load_all_zip_lines(zip_dir):
    """
    读取文件夹下所有 zip，并按照 START 时间排序。
    """
    zip_paths = sorted(zip_dir.glob("*.zip"))

    if len(zip_paths) == 0:
        raise FileNotFoundError(f"在 {zip_dir.resolve()} 没有找到 zip 文件。")

    sort_keys = []
    for p in zip_paths:
        st = get_start_time_from_zip(p)
        if st is None:
            st = p.name
        sort_keys.append(st)

    zip_paths = [p for _, p in sorted(zip(sort_keys, zip_paths), key=lambda x: x[0])]

    times = []
    lines = []

    print("读取到的 zip 文件顺序：")
    for p in zip_paths:
        t, b = read_one_magnetometer_zip(p)
        times.append(t)
        lines.append(b)
        print(f"  {p.name}: {len(b)} samples, duration = {t[-1] - t[0]:.3f} s")

    return zip_paths, times, lines


# =========================
# 2. 起止点剔除
# =========================

def estimate_sampling_rate(t):
    """
    根据时间戳估计采样频率。
    """
    dt = np.diff(t)
    dt = dt[np.isfinite(dt) & (dt > 0)]

    if len(dt) == 0:
        return 100.0

    return 1.0 / np.median(dt)


def rolling_mean(x, window):
    """
    简单滑动平均。
    """
    if window <= 1:
        return x

    kernel = np.ones(window) / window
    return np.convolve(x, kernel, mode="same")


def auto_trim_static_segment(t, b, fixed_trim_sec=0.20, auto_static_trim=True):
    """
    剔除起点和终点。

    逻辑：
    1. 先固定去掉起点和终点各 fixed_trim_sec 秒。
       这是最稳定的，因为你知道开关设备时容易停顿。
    2. 如果 auto_static_trim=True，再用磁场变化率判断是否存在明显静止段。
       静止段的代理指标是 |dB/dt| 的滑动平均。
       如果头部或尾部持续处于低变化率，就进一步剔除。

    注意：
    只有磁力计时，不能百分百判断人是否静止。
    所以这里的自动判断只做保守修剪，不会大幅删除数据。
    """
    t = np.asarray(t, dtype=float)
    b = np.asarray(b, dtype=float)

    fs = estimate_sampling_rate(t)
    n_fixed = int(round(fixed_trim_sec * fs))

    if len(b) <= 2 * n_fixed + 10:
        return t, b

    start = n_fixed
    end = len(b) - n_fixed

    if not auto_static_trim:
        return t[start:end], b[start:end]

    b_mid = b[start:end]
    t_mid = t[start:end]

    if len(b_mid) < 100:
        return t_mid, b_mid

    # 用磁场变化率作为“是否在动”的弱指标
    db = np.abs(np.diff(b_mid, prepend=b_mid[0]))
    win = max(5, int(round(0.30 * fs)))     # 0.30 s 滑动窗口
    score = rolling_mean(db, win)

    # 低变化率阈值：取全局较低分位数，但不能太激进
    threshold = np.quantile(score, 0.15)

    min_static = max(5, int(round(0.40 * fs)))  # 至少持续 0.40 s 才认为是停顿段

    # 头部静止段判断
    head_cut = 0
    count = 0
    for i in range(len(score)):
        if score[i] <= threshold:
            count += 1
        else:
            if count >= min_static:
                head_cut = i
            break

    # 尾部静止段判断
    tail_cut = len(score)
    count = 0
    for i in range(len(score) - 1, -1, -1):
        if score[i] <= threshold:
            count += 1
        else:
            if count >= min_static:
                tail_cut = i + 1
            break

    # 保守限制：自动修剪最多只额外修掉 1 秒
    max_extra = int(round(1.00 * fs))

    head_cut = min(head_cut, max_extra)
    tail_cut = max(tail_cut, len(score) - max_extra)

    if tail_cut <= head_cut + 20:
        return t_mid, b_mid

    return t_mid[head_cut:tail_cut], b_mid[head_cut:tail_cut]


def trim_all_lines(times, lines):
    trimmed_lines = []
    trimmed_times = []

    print("\n起止点剔除：")
    for i, (t, b) in enumerate(zip(times, lines)):
        tt, bb = auto_trim_static_segment(
            t,
            b,
            fixed_trim_sec=FIXED_TRIM_SEC,
            auto_static_trim=AUTO_STATIC_TRIM,
        )
        trimmed_times.append(tt)
        trimmed_lines.append(bb)
        print(f"  line {i + 1}: {len(b)} -> {len(bb)} samples")

    return trimmed_times, trimmed_lines


# =========================
# 3. 统一采样点数
# =========================

def resample_to_length_1d(x, target_len):
    """
    从一条线中按等间距索引抽取 target_len 个点。
    这对应你的实验假设：匀速运动，采样点沿线均匀分布。
    """
    x = np.asarray(x, dtype=float)

    if len(x) < target_len:
        raise ValueError("target_len 不能大于原始长度。")

    idx = np.linspace(0, len(x) - 1, target_len, dtype=int)
    return x[idx]


def equalize_line_lengths(lines):
    """
    保证每条线采样点数量一致。
    取最短线长度作为基准，其余线等间距抽取。
    """
    min_len = min(len(line) for line in lines)

    equalized = []
    for line in lines:
        equalized.append(resample_to_length_1d(line, min_len))

    equalized = np.asarray(equalized, dtype=float)

    print(f"\n统一采样点数：")
    print(f"  min length = {min_len}")
    print(f"  equalized shape = {equalized.shape}")

    return equalized


# =========================
# 4. 异常测线剔除
# =========================

def safe_corr(a, b):
    """
    计算相关系数，避免常数序列导致 NaN。
    """
    a = np.asarray(a, dtype=float)
    b = np.asarray(b, dtype=float)

    if np.std(a) < 1e-12 or np.std(b) < 1e-12:
        return 0.0

    c = np.corrcoef(a, b)[0, 1]

    if not np.isfinite(c):
        return 0.0

    return float(c)


def pairwise_best_corr(lines):
    """
    计算测线两两之间的最大相关性。
    对每一对线，同时比较原方向和反转方向。
    """
    n = lines.shape[0]
    corr = np.eye(n)

    for i in range(n):
        for j in range(i + 1, n):
            c1 = abs(safe_corr(lines[i], lines[j]))
            c2 = abs(safe_corr(lines[i], lines[j][::-1]))
            c = max(c1, c2)

            corr[i, j] = c
            corr[j, i] = c

    return corr


def drop_outlier_lines_if_needed(lines, zip_paths, target_n_lines):
    """
    如果 zip 数量多于目标测线数量，则自动删除平均相关性最低的线。

    例如你传入 9 个 zip，但有效建图只需要 8 条线，
    就删除和其它线最不像的一条。
    """
    if target_n_lines is None:
        return lines, zip_paths

    if lines.shape[0] <= target_n_lines:
        return lines, zip_paths

    lines = np.asarray(lines, dtype=float)
    zip_paths = list(zip_paths)

    while lines.shape[0] > target_n_lines:
        corr = pairwise_best_corr(lines)
        avg_corr = (np.sum(corr, axis=1) - 1.0) / (corr.shape[0] - 1)

        drop_idx = int(np.argmin(avg_corr))

        print("\n检测到测线数量多于目标数量，自动剔除一条异常/多余测线：")
        for i, p in enumerate(zip_paths):
            print(f"  line {i + 1}: avg corr = {avg_corr[i]:.3f}, file = {p.name}")

        print(f"  -> drop line {drop_idx + 1}: {zip_paths[drop_idx].name}")

        lines = np.delete(lines, drop_idx, axis=0)
        zip_paths.pop(drop_idx)

    print(f"\n最终用于建图的测线数量：{lines.shape[0]}")
    print("最终使用文件：")
    for p in zip_paths:
        print(f"  {p.name}")

    return lines, zip_paths


# =========================
# 5. 测线方向校正
# =========================

def align_line_directions(lines):
    """
    自动判断每条线是否需要反转。

    逻辑：
    第 1 条线作为基准。
    从第 2 条线开始，与上一条已经校正好的线比较：
    - 如果原方向相关性更高，保持不变；
    - 如果反转方向相关性更高，就反转。
    """
    lines = np.asarray(lines, dtype=float)

    aligned = [lines[0]]
    flip_flags = [False]

    for i in range(1, lines.shape[0]):
        ref = aligned[-1]
        cur = lines[i]

        c_same = abs(safe_corr(ref, cur))
        c_reverse = abs(safe_corr(ref, cur[::-1]))

        if c_reverse > c_same:
            aligned.append(cur[::-1])
            flip_flags.append(True)
        else:
            aligned.append(cur)
            flip_flags.append(False)

    aligned = np.asarray(aligned, dtype=float)

    print("\n测线方向校正：")
    for i, flag in enumerate(flip_flags):
        print(f"  line {i + 1}: {'reversed' if flag else 'original'}")

    return aligned, flip_flags


# =========================
# 6. 测线矩阵 -> N×3 点云
# =========================

def line_array_to_point_cloud(
    line_array,
    line_gap=0.96,
    line_length=8.08,
    target_dy=0.05,
):
    """
    将测线矩阵转换为 Kriging 需要的 N×3 点云。

    line_array:
        shape = (n_lines, n_samples)

    返回:
        points:
            shape = (N, 3)，每行是 [x, y, B]

        meta:
            地图物理范围
    """
    B = np.asarray(line_array, dtype=float)

    if B.ndim != 2:
        raise ValueError("line_array 必须是二维数组。")

    n_lines, n_raw = B.shape

    y_raw = np.linspace(0.0, line_length, n_raw)

    if target_dy is not None and target_dy > 0:
        n_keep = int(np.floor(line_length / target_dy)) + 1
        idx = np.linspace(0, n_raw - 1, n_keep, dtype=int)

        B = B[:, idx]
        y = y_raw[idx]
    else:
        y = y_raw

    x = np.arange(n_lines, dtype=float) * line_gap

    X, Y = np.meshgrid(x, y, indexing="ij")

    points = np.column_stack([
        X.ravel(),
        Y.ravel(),
        B.ravel(),
    ])

    meta = {
        "x_min": float(x.min()),
        "x_max": float(x.max()),
        "y_min": float(y.min()),
        "y_max": float(y.max()),
        "n_lines": int(n_lines),
        "n_samples_per_line": int(len(y)),
    }

    print("\n点云生成：")
    print(f"  line_array shape = {line_array.shape}")
    print(f"  point cloud shape = {points.shape}")
    print(f"  x range = [{meta['x_min']:.3f}, {meta['x_max']:.3f}] m")
    print(f"  y range = [{meta['y_min']:.3f}, {meta['y_max']:.3f}] m")

    return points, meta


# =========================
# 7. Kriging 建图
# =========================

def build_kriging_map(
    points,
    meta,
    grid_resolution=0.02,
    len_x=1.20,
    len_y=0.25,
    nugget_ratio=0.02,
):
    points = np.asarray(points, dtype=float)

    x = points[:, 0]
    y = points[:, 1]
    z = points[:, 2]

    gridx = np.arange(
        meta["x_min"],
        meta["x_max"] + grid_resolution,
        grid_resolution,
    )

    gridy = np.arange(
        meta["y_min"],
        meta["y_max"] + grid_resolution,
        grid_resolution,
    )

    field_var = float(np.var(z))

    if field_var <= 0:
        raise ValueError("磁场数据方差为 0，无法建图。")

    cov_model = Gaussian(
        dim=2,
        len_scale=len_x,
        anis=len_y / len_x,
        angles=0.0,
        var=field_var,
        nugget=nugget_ratio * field_var,
    )

    OK = OrdinaryKriging(
        x,
        y,
        z,
        variogram_model=cov_model,
        enable_plotting=False,
        verbose=False,
    )

    z_map, ss_map = OK.execute("grid", gridx, gridy)

    map_dict = {
        "map": np.asarray(z_map),
        "variance": np.asarray(ss_map),
        "gridx": gridx,
        "gridy": gridy,
        "rangex_min": meta["x_min"],
        "rangex_max": meta["x_max"],
        "rangey_min": meta["y_min"],
        "rangey_max": meta["y_max"],
        "grid_resolution": grid_resolution,
        "len_x": len_x,
        "len_y": len_y,
        "nugget_ratio": nugget_ratio,
    }

    print("\nKriging 建图完成：")
    print(f"  map shape = {map_dict['map'].shape}")
    print(f"  gridx length = {len(gridx)}")
    print(f"  gridy length = {len(gridy)}")

    return map_dict


# =========================
# 8. 绘图与保存
# =========================

def plot_map(map_dict, output_png=None):
    plt.figure(figsize=(8, 8))

    plt.imshow(
        map_dict["map"],
        origin="lower",
        extent=[
            map_dict["rangex_min"],
            map_dict["rangex_max"],
            map_dict["rangey_min"],
            map_dict["rangey_max"],
        ],
        aspect="equal",
    )

    plt.xlabel("x / m")
    plt.ylabel("y / m")
    plt.title("Own Geomagnetic Map with Anisotropic Kriging")
    plt.colorbar(label="Magnetic field / µT")
    plt.tight_layout()

    if output_png is not None:
        plt.savefig(output_png, dpi=300)
        print(f"\n已保存磁图图片：{output_png}")

    plt.show()


def plot_variance(map_dict):
    plt.figure(figsize=(8, 8))

    plt.imshow(
        map_dict["variance"],
        origin="lower",
        extent=[
            map_dict["rangex_min"],
            map_dict["rangex_max"],
            map_dict["rangey_min"],
            map_dict["rangey_max"],
        ],
        aspect="equal",
    )

    plt.xlabel("x / m")
    plt.ylabel("y / m")
    plt.title("Kriging Estimation Variance")
    plt.colorbar(label="Kriging variance")
    plt.tight_layout()
    plt.show()


def save_map_npz(map_dict, output_npz):
    np.savez(
        output_npz,
        mag_map=map_dict["map"],
        variance=map_dict["variance"],
        gridx=map_dict["gridx"],
        gridy=map_dict["gridy"],
        rangex_min=map_dict["rangex_min"],
        rangex_max=map_dict["rangex_max"],
        rangey_min=map_dict["rangey_min"],
        rangey_max=map_dict["rangey_max"],
        grid_resolution=map_dict["grid_resolution"],
        len_x=map_dict["len_x"],
        len_y=map_dict["len_y"],
        nugget_ratio=map_dict["nugget_ratio"],
    )

    print(f"已保存建图结果：{output_npz}")


# =========================
# 9. 主程序
# =========================

if __name__ == "__main__":
    zip_paths, times, raw_lines = load_all_zip_lines(ZIP_DIR)

    trimmed_times, trimmed_lines = trim_all_lines(times, raw_lines)

    line_array = equalize_line_lengths(trimmed_lines)

    line_array, used_zip_paths = drop_outlier_lines_if_needed(
        line_array,
        zip_paths,
        target_n_lines=TARGET_N_LINES,
    )

    line_array, flip_flags = align_line_directions(line_array)

    points, meta = line_array_to_point_cloud(
        line_array,
        line_gap=LINE_GAP,
        line_length=LINE_LENGTH,
        target_dy=TARGET_DY,
    )

    map_dict = build_kriging_map(
        points,
        meta,
        grid_resolution=GRID_RESOLUTION,
        len_x=LEN_X,
        len_y=LEN_Y,
        nugget_ratio=NUGGET_RATIO,
    )

    save_map_npz(map_dict, OUTPUT_NPZ)

    plot_map(map_dict, output_png=OUTPUT_PNG)

    plot_variance(map_dict)