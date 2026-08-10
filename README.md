# Lego-like Geomagnetic Positioning

![Python](https://img.shields.io/badge/Python-3.11%2B-blue)
![License](https://img.shields.io/badge/License-MIT-green)


# 运行方式：
## Mac端：
```bash
bash run.sh
```

## Windows 端：

双击 `run.bat`。脚本会在项目根目录自动创建 `.venv`、安装依赖并打开 Bokeh 页面。

只配置环境、不启动网页服务：

```bat
run.bat --setup-only
```

### PyCharm

1. 在 PyCharm 中选择 `File -> Open`，打开本项目根目录。
2. 项目已配置为使用 `.venv\Scripts\python.exe`。
3. 运行网页界面时，在 PyCharm Terminal 中执行 `run.bat`；运行算法入口时直接运行 `main.py`。

# First of All
This is the package I am using for testing my own geomagnetic positioning project using Particle filter, and I am trying to make the project **more lego-like such as pytorch** , and you can see some of the characteristics are from pytorch, actually. I am going to make this a acedemic-directed tool, 
everyone who come up with an idea of, whatever the filter problem is, can immediately turn on the mac, quickly have a simulation, and feel free to build anything you like. World of Machine Learning can do it, I hope we will do it. 

Although the algorithm I've written in the `main.py` is still dumb and I am still seeking the reason why it is performing below my expectation, however, like someone said on Youtube, I am the guy interested in building shovels, and I hope there will be more contributers can participate in it. 

The project per se is just in testing right now, with so many functional issues yet to be finished, but I hope this project, conversely, will never be an end, with firm cooperation of the intelligence of the community. **A project with continuous maintainence and contributers is a healthy project.**


For anyone who is interested, email `kudoumakoto6523@gmail.com` (same as the github account).

## Below are the content.

Lego-style geomagnetic indoor positioning for fast academic prototyping.

This repository is a testing package for geomagnetic positioning with particle filtering, IMU-based PDR, and DDTW-oriented magnetic matching. The current algorithm in [`main.py`](main.py) is still experimental and its performance is not yet where I want it to be, but the goal of the project is already clear:

- make geomagnetic positioning experiments easy to assemble
- expose reusable building blocks instead of one hard-coded pipeline
- let researchers quickly try a new filter idea, run a simulation, and inspect results

The project is closer to "building shovels" than claiming a finished localization system. If PyTorch can give machine learning researchers a flexible toolbox, this project aims to do something similar for geomagnetic and filter-oriented indoor positioning research.

## Status

This project is under active restructuring.

- The package layout is already modular and usable for experiments.
- The particle-filter pipeline is configurable and reorderable.
- UJI map building and visualization are implemented.
- Some algorithm hooks are still placeholders or baseline implementations.
- End-to-end accuracy is still being improved.

If you are interested in contributing ideas, code, experiments, or criticism, contributions are welcome.

## What The Repository Currently Covers

The current pipeline combines:

- IMU-based PDR for step and heading estimation
- geomagnetic matching
- particle filtering with DDTW-oriented weighting design
- UJIIndoorLoc-Mag map building
- direct user-defined magnetic map input

## Installation

Python `>= 3.11` is required.

```bash
python -m venv .venv
# macOS/Linux: source .venv/bin/activate
# Windows: .venv\Scripts\activate
python -m pip install -e .
```

Current package dependencies from [`pyproject.toml`](pyproject.toml):

- `numpy`
- `pykrige`
- `matplotlib`
- `gstools`
- `bokeh`

## Quick Start

Run the default experiment:

```bash
python main.py
```

Test UJI map building plus user-map visualization:

```bash
python main_get_map_temp.py
```

Plot true route overlays:

```bash
python main_get_true_route.py
```

Inspect sensor streams and visualization outputs:

```bash
python main_get_sensor_and_len.py
```

## Design Goal: Lego-Style Pipeline

The package is intentionally structured in a PyTorch-like style. Instead of forcing one giant script, it separates:

- orchestration
- state models
- block registries
- configurable PDR modules
- configurable PF modules

The default flow is:

`Initializer -> RunContext -> Experiment -> GeomagPipeline`

Basic example:

```python
from Geomag import Experiment, Initializer, PDRConfig, PFConfig

ctx = Initializer(
    num_runs=1,
    window_size=400,
    route_source="uji",
    sensor_source="uji",
    uji_test_file="tt01.txt",
).create_context()

pdr = PDRConfig(
    step_judge="peak_dynamic",
    step_judge_params={"peak_sigma": 0.40, "peak_prominence": 0.16},
    step_length="weinberg",
    step_length_params={"weinberg_k": 0.45},
    heading="gyro",
    heading_params={"dt": 0.02},
    mag="norm_mean",
)

pf = PFConfig(
    state_params={"num_particles": 500, "min_particles": 120, "max_particles": 5000},
    motion="gaussian",
    motion_params={"heading_noise_std": 0.10, "step_noise_std": 0.20},
    weight="ddtw",
    weight_params={"sigma": 6.0, "max_hist": 80},
    particle_size="kld",
    particle_size_params={"epsilon": 0.10},
    resample_trigger="ess_or_target",
    resample_trigger_params={"ess_ratio_threshold": 0.45},
    resample="cso",
)

result = Experiment(ctx, pdr_config=pdr, pf_config=pf).run(show=True)
```

## Reordering The Particle Filter

The PF side is built from composable stages, so you can rearrange them when needed.

```python
from Geomag import (
    ParticleSizeStage,
    PredictStage,
    ResampleDecisionStage,
    ResampleStage,
    UpdateStage,
    build_pf_sequential,
)

pf = build_pf_sequential(
    ("predict", PredictStage(motion="gaussian")),
    ("particle_size", ParticleSizeStage(particle_size="kld")),
    ("update", UpdateStage(weight="ddtw")),
    ("resample_decision", ResampleDecisionStage(trigger="ess_or_target")),
    ("resample", ResampleStage(resample="cso")),
)
```

You can inspect the registered blocks at runtime:

```python
from Geomag import Experiment, GeomagPipeline

print(GeomagPipeline.available_blocks())
print(Experiment.describe_api())
```

Current block families include:

- `step_judge`: `autocorr`, `frequency_fft`, `peak_dynamic`, `peak_fixed`, `valley_peak`, `zero_crossing`
- `step_length`: `fixed`, `weinberg`
- `heading`: `gyro`, `q_fused`, `tilt_compass`
- `mag`: `norm_last`, `norm_mean`
- `motion`: `gaussian`
- `weight`: `ddtw`
- `particle_size`: `kld`
- `resample_trigger`: `always`, `ess_or_target`
- `resample`: `cso`

## Map API

[`Geomag/algorithms.py`](Geomag/algorithms.py) exposes the public map entrypoint:

```python
from Geomag.algorithms import get_map
```

Three map sources are currently supported:

- `source="uji"`: build a continuous map from UJIIndoorLoc-Mag
- `source="own"`: use a user-defined magnetic map, with direct matrix input preferred
- `source="outdoor"`: build the outdoor RTK map from map-building sessions

The experiment runner has three isomorphic branches: `uji`, `own`, and
`outdoor`. The outdoor branch uses the map-building data for its map and keeps
the navigation data separate as the test stream.

### Outdoor RTK Map

Only the 17 `Geomagnetic Map Building` archives and `LOG00040.TXT` are used.
Because the RTK log contains positions beyond the survey itself, the builder
first derives the survey time range from each archive's `meta/time.csv`, then
keeps only overlapping RTK GGA records with fix quality 4. Sensor samples are
aggregated into 0.15 m cells and the entire rectangular map is filled at 0.10 m
spacing using PyKrige Ordinary Kriging with an exponential variogram.

```python
from Geomag.algorithms import get_map

outdoor_map = get_map(
    source="outdoor",
    outdoor_force_rebuild=True,
)
print(outdoor_map["output_png"])
```

Configuration lives under `[tool.outdoor_map_builder]` in `pyproject.toml`.
The standalone map-only example is `examples/main_outdoor_map.py`.

### Outdoor RTK Test Branch

The five `Geomagnetic Navigation` archives are outdoor test runs. Their
accelerometer, gyroscope, and magnetometer channels are synchronized on the
accelerometer time axis. `LOG00042.TXT` is aligned to the same timestamps and
provides the RTK ground-truth route. Only checksum-valid RTK fix-quality-4
positions are used.

Data roles are kept separate:

- `Geomagnetic Map Building` + `LOG00040.TXT`: build the outdoor magnetic map
- `Geomagnetic Navigation`: positioning-test sensor input
- `LOG00042.TXT`: positioning-test RTK ground truth

Run a test from the command line:

```bash
python main.py --branch outdoor --outdoor nav1 --no-show
```

Available selectors are `nav1` through `nav5` (or `1` through `5`). For a quick
pipeline check, append `--max-frames 300`. A code example is available at
`examples/main_outdoor_simulation.py`.

### DeepSeek Automatic Parameter Tuning

The backend can run an iterative `evaluate -> suggest -> validate -> rerun ->
keep best` loop for the outdoor branch. DeepSeek receives downsampled but real
RTK/PDR/PF coordinates, error statistics, the current PDR/PF configuration, and
previous-trial results. It returns JSON parameter patches only; generated code
is never executed. The prompt treats PDR as the primary tuning target: it first
optimizes step detection, step length, and heading against RTK, and only tunes
PF parameters after PDR has converged or the remaining error is clearly PF-side.

The API-key slot is intentionally blank in
`config/deepseek_auto_tuning.json`. Prefer setting the key through the
environment so it cannot be committed accidentally:

```powershell
$env:DEEPSEEK_API_KEY="your-key-here"
```

The default model is the lower-cost `deepseek-v4-flash`, with thinking disabled
for this bounded task. Preview the complete request without contacting DeepSeek:

```bash
python main.py --branch outdoor --outdoor nav4 --max-frames 300 --tune-dry-run --no-show
```

Run three model-guided proposals (four experiment trials including baseline):

```bash
python main.py --branch outdoor --outdoor nav4 --auto-tune --tune-iterations 3 --no-show
```

The public one-step backend interface is `Geomag.suggest_parameters(...)`.
Only whitelisted numeric PDR/PF parameters are accepted, with type and range
checks plus `min_particles <= num_particles <= max_particles` validation.
Reports are written below `data/processed/outdoor_rtk_map/auto_tuning/`.

Privacy note: enabling live tuning sends the downsampled local-coordinate RTK,
PDR, and PF trajectories in the prompt to the configured DeepSeek API endpoint.

### UJI Branch

```python
from Geomag.algorithms import get_map

uji_map = get_map(source="uji")
print(uji_map)
```

Behavior:

- downloads the UJI zip if missing
- extracts the dataset if missing
- parses `lines/` and `curves/`
- reconstructs sample positions
- fits an Ordinary Kriging model
- writes processed artifacts
- returns a metadata dictionary

Configuration lives in [`pyproject.toml`](pyproject.toml) under `[tool.map_builder]`.

Relevant keys:

- `preview_resolution`
- `max_kriging_points`
- `seed`
- `variogram_model`
- `output_model_npz`
- `output_preview_npz`
- `output_json`
- `output_png`

Typical returned fields include:

- `source`
- `continuous_map`
- `output_model_npz`
- `output_preview_npz`
- `output_json`
- `output_png`
- `zip_path`
- `extract_dir`

### Own Branch

Preferred input is a directly editable 2D matrix:

```python
from Geomag.algorithms import get_map

own_map = get_map(
    source="own",
    own_grid_array=[
        [45.10, 45.22, 45.31],
        [44.97, 45.05, 45.27],
        [44.83, 44.96, 45.14],
    ],
    own_grid_meta={
        "cell_size_m": 0.5,
        "origin_xy_m": [0.0, 0.0],
        "variogram_model": "spherical",
    },
)
print(own_map)
```

Important metadata:

- `cell_size_m`: distance between neighboring cells in meters
- `origin_xy_m`: physical origin for mapping matrix indices to world coordinates
- optional `variogram_model`: interpolation choice for visualization

Matrix convention:

- `matrix[row][col]` stores magnetic magnitude
- `x = origin_x + col * cell_size`
- `y = origin_y + row * cell_size`

## Visualization

Use `visualize(...)` with mode selection:

```python
from Geomag.algorithms import visualize
```

UJI map preview:

```python
visualize(geomag_map=uji_map, mode="ujimap")
```

User map preview:

```python
visualize(geomag_map=own_map, mode="usermap")
```

The visualization API also supports route and sensor overlays, as shown in:

- [`main_get_map_temp.py`](examples/main_get_map_temp.py)
- [`main_get_true_route.py`](examples/main_get_true_route.py)
- [`main_get_sensor_and_len.py`](examples/main_get_sensor_and_len.py)

## Repository Layout

- [`main.py`](main.py): thin runtime entrypoint
- [`Geomag/initiation.py`](Geomag/initiation.py): initialization orchestration
- [`Geomag/experiment.py`](Geomag/experiment.py): experiment loop wrapper
- [`Geomag/pipeline.py`](Geomag/pipeline.py): composable PDR and PF pipeline
- [`Geomag/models.py`](Geomag/models.py): shared state classes such as `PFState`, `Particle`, and `RunContext`
- [`Geomag/blocks.py`](Geomag/blocks.py): block interfaces and registries
- [`Geomag/algorithms.py`](Geomag/algorithms.py): map building, visualization, and algorithm implementations/placeholders
- [`Geomag/nn.py`](Geomag/nn.py): lightweight `Module` and `Sequential` abstractions
- [`Geomag/utils.py`](Geomag/utils.py): helper utilities for sensor collection and map loading

## Notes

- If `pykrige` is missing, continuous interpolation will fail with an explicit installation message.
- If `matplotlib` is missing, visualization will fail with an explicit installation message.
- The initializer tries to build the UJI map first and can fall back to existing processed artifacts if rebuilding is unavailable.
- The first `get_map(source="uji")` call may download the dataset automatically.

## Roadmap

Near-term goals:

- improve particle-filter performance and diagnostics
- replace baseline or placeholder parts with stronger research-grade implementations
- make more blocks plug-and-play
- add more examples, tests, and benchmark scripts
- make the package easier for outside contributors to extend

## Contributing

This project is meant to keep evolving. If you have:

- a new filter idea
- a better weighting method
- a stronger step detector
- a new dataset adapter
- a cleaner interface design

then this repository is intended to be a place where that idea can be tested quickly.

Pull requests, issue reports, design suggestions, and academic collaboration are all welcome.

## Contact

For collaboration or questions, email:

`kudoumakoto6523@gmail.com`
