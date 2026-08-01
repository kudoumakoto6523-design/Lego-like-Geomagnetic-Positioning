# Lego-like Geomagnetic Positioning

![Python](https://img.shields.io/badge/Python-3.8%2B-blue)
![License](https://img.shields.io/badge/License-MIT-green)

> **注意**：不要跑 `route1_run1`，传感器数据与注册路线对不上（实际忘了当时测的是哪个路线了）。默认正式评测使用 `route1_run2`、`route2_run2`；`route2_run1` 保留为可单独运行的重复采集。

# 运行方式：
## Mac端：
```bash
bash run.sh
```

## Windows 端：

直接运行 `run.bat`
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

### Own-data baseline

The default own-data profile now applies a 0.40 s step cooldown, continuous
timestamp-based gyro integration, a trainable adaptive step model, online
gyroscope bias estimation, particle-level step-scale/heading-bias estimation,
turn-aware noise, a tighter known-start prior, and bilinear lookup on the
regular magnetic grid. On the two manifest-selected primary captures:

| Dataset | PF mean / P95 | Cross-track mean | Endpoint |
|---|---:|---:|---:|
| `route1_run2` | 1.141 m / 1.923 m | 0.335 m | 0.664 m |
| `route2_run2` | 0.893 m / 1.535 m | 0.413 m | 1.185 m |

These figures use the registered route geometry and align each detected step
with the fraction of the automatically detected active walking interval. This
matches the controlled constant-speed acquisition protocol while excluding
recording time before the first step or after the last step. They are useful
regression metrics, not centimeter-accurate ground truth.

Use the legacy full-recording alignment for comparison:

```bash
python main.py --own route1_run2 --own-alignment-mode capture_time --no-show
```

A stateful quaternion attitude estimator is also available for ablation. It
propagates the full three-axis gyroscope, constrains roll/pitch with gravity,
requires consecutive stationary evidence before estimating three-axis bias,
and optionally applies norm/direction-gated magnetic yaw correction:

```bash
python main.py --own route1_run2 --own-heading-method quaternion --no-show
python main.py --own route1_run2 --own-heading-method quaternion \
  --own-quaternion-use-magnetometer --no-show
```

It is not the default. On the legacy captures, quaternion gravity fusion
worsens `route1_run2` and does not recover the missing 90-degree yaw in
`route2_run1`; magnetic correction is also unreliable in the indoor field.
The gyro baseline remains the honest default until captures with verified
stationary calibration are available.

Own-data plots now distinguish the raw PF estimate (magenta) from its causal
EMA output (cyan). A second diagnostics image contains heading, step length,
ESS, posterior step scale/heading bias, and magnetic residual histories.
The selected captures use a low-lag EMA history weight of `0.30`. The previous
`0.70` setting over-smoothed the shorter `route2_run2`: reducing it lowers the
selected-pair aligned mean from `1.181` to `1.017 m` and endpoint mean from
`1.046` to `0.925 m`, while cross-track mean increases from `0.319` to
`0.374 m`. The strong EMA's unusually small `route1_run2` endpoint error was
partly a closed-loop artefact: on the return to the start area, lag pulled the
reported endpoint toward earlier positions. The lower weight is a more honest
real-time default. Smoothing can be disabled to inspect the filter directly:

```bash
python main.py --own route1_run2 --own-pf-smoothing-alpha 0 --no-show
```

The motion-prediction residual smoother is also available with
`--own-pf-smoothing-mode motion_adaptive`, but it remains experimental. It
improves `route2_run2` while making `route1_run2` worse and increasing the
selected pair's average cross-track error, so the causal EMA remains the
default.

Captures made with the phone facing forward while walking on orthogonal tile
seams can enable a 90-degree Manhattan heading prior:

```bash
python main.py --own route2_run1 --own-heading-snap-deg 90 --no-show
```

This is intentionally opt-in. Its hysteretic turn detector reduces
`route2_run1` smoothed aligned mean from 0.772 m to 0.691 m and cross-track
mean from 0.426 m to 0.073 m; the raw PF aligned mean becomes 0.217 m.
It still worsens the endpoint on `route1_run2`, where the final segment is
estimated as 6.21 m instead of 3.84 m. Enabling it globally would hide a
step/turn-timing calibration problem.

The adaptive step model exposes cadence and acceleration-variability
coefficients, but both remain zero until they can be fitted on separate
calibration captures:

```bash
python main.py --own route2_run1 \
  --own-step-weinberg-k 0.31 \
  --own-step-cadence-weight 0.0 \
  --own-step-variability-weight 0.0 \
  --no-show
```

Use `--no-own-pf-joint-calibration` for an ablation without per-particle
`step_scale` and `heading_bias`.

Walking speed changes the personalized Weinberg coefficient. Calibrate the
step scale on a separate straight, known-distance walk, then apply it to later
captures:

```bash
python -m Geomag.step_calibration \
  results/calibration_walk.json --known-distance-m 10 \
  --output-json results/step_calibration.json

python main.py --own route2_run2 \
  --own-step-length-scale 1.12 --no-show
```

Do not estimate the scale from the same route used for evaluation. Doing that
on `route2_run2` gives an oracle scale of `1.2685` and improves mean /
cross-track / endpoint errors from `0.893 / 0.413 / 1.185 m` to
`0.327 / 0.145 / 0.223 m`, but those values are only an upper-bound diagnosis
because the known route length was leaked into the estimator.

An independent known-turn capture can likewise calibrate the gyroscope rate
scale. `route1_run2` integrates to `-284.503°` around device Z for a registered
`-270°` route, giving `270 / 284.503 = 0.9490`. Applying that frozen value only
to `route2_run2` improves aligned / cross-track / endpoint error from
`0.893 / 0.413 / 1.185 m` to `0.861 / 0.384 / 1.060 m`:

```bash
python main.py --own route2_run2 \
  --own-gyro-rate-scale 0.9490245178 --no-show
```

For a repeatedly surveyed route, an experimental causal progress matcher can
use a prior independent run as a three-axis magnetic-change template:

```bash
python -m Geomag.batch_evaluation route2_run1 \
  --output-dir results/route2_run1_progress_template --no-plots

python main.py --own route2_run2 \
  --own-gyro-rate-scale 0.9490245178 \
  --own-progress-template-json \
    results/route2_run1_progress_template/route2_run1.json \
  --own-progress-correction-gain 0.30 --no-show
```

That exploratory combination reaches `0.837 / 0.380 / 0.819 m`, but the
matcher and gain were developed while inspecting `route2_run2`. It is disabled
by default and needs a third independent repeat before it can be reported as a
held-out improvement. It also applies only to a previously surveyed route, not
arbitrary indoor walking.

Map translation can be tested with `--own-map-offset-x-m` and
`--own-map-offset-y-m`. The route1-selected magnetic-shape offset worsened the
held-out route2 result, so the registered default remains `(0, 0)`.

The hybrid DDTW + absolute-level + gradient likelihood is implemented in
`DDTWWeight`, but remains opt-in: ablation on the current map reduced some final
errors while worsening mean error, so shape-only DDTW remains the default.
See [`docs/2026-07-29-own-data-improvement-report.md`](docs/2026-07-29-own-data-improvement-report.md)
for the full ablation and evaluation notes.

Before comparing algorithm changes, audit the acquisition quality and run the
same configuration over every enabled own-data capture:

```bash
python -m Geomag.data_quality
python -m Geomag.batch_evaluation
```

The operator confirmed that `route2_run2` is an independent second capture of
the same route as `route2_run1`. The manifest-selected default evaluation now
uses `route1_run2` and `route2_run2`; `route2_run1` remains runnable as a
separate repeat:

```bash
python -m Geomag.route_identity
python -m Geomag.batch_evaluation --no-plots
python -m Geomag.batch_evaluation route2_run1 --no-plots
```

Own-map geometry is explicit. Compare the measured survey grid and the
tile-coordinate interpretation independently:

```bash
python -m Geomag.batch_evaluation \
  --map-profile survey_kriging --output-dir results/map_profile_survey --no-plots
python -m Geomag.batch_evaluation \
  --map-profile tile_manifest --output-dir results/map_profile_tiles --no-plots
```

The original eight magnetometer-only survey archives can now be reconstructed
as a three-axis map and used as a yaw-aligned, anomaly-gated PF likelihood:

```bash
python -m Geomag.vector_map
python -m Geomag.batch_evaluation \
  --map-profile survey_kriging --vector-map \
  --vector-weight 0.10 --output-dir results/vector_survey_w010 --no-plots
```

This option is deliberately disabled by default. The survey archives contain
no synchronized phone attitude, so the run calibrates one relative yaw offset
at its first valid step. Later updates use horizontal magnetic direction only;
norm or direction anomalies fall back to scalar DDTW. With weight `0.10`, the
selected survey-profile aligned means change from `1.270 / 0.895 m` to
`1.212 / 0.901 m` for `route1_run2 / route2_run2`. Aggregate cross-track error
improves from `0.400` to `0.384 m`, while aggregate endpoint error changes from
`1.065` to `1.085 m`;
this is useful evidence, not yet a production default.

Passing dataset keys explicitly always overrides the manifest-selected default.
`--all-evaluable` runs every evaluable capture, including the secondary
`route2_run1` repeat. The older `--include-provisional` spelling remains an
alias.

The audit checks required files, timestamps, stream overlap, sample gaps,
start/end stationary windows, route duration, and device-Z yaw consistency.
It also blocks the known-invalid `route1_run1`. New captures should use
[`data/own_data_package/capture_metadata.template.json`](data/own_data_package/capture_metadata.template.json)
and record synchronized turn events. These metadata requirements also support
arbitrary real routes; the 90-degree tile route remains an optional controlled
test rather than a production assumption.

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
source .venv/bin/activate
pip install -e .
```

Current package dependencies from [`pyproject.toml`](pyproject.toml):

- `numpy`
- `pykrige`
- `matplotlib`

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
- `step_length`: `adaptive`, `fixed`, `weinberg`
- `heading`: `gyro`, `q_fused`, `tilt_compass`
- `mag`: `norm_last`, `norm_mean`
- `motion`: `gaussian`
- `weight`: `ddtw`
- `particle_size`: `kld`
- `resample_trigger`: `always`, `ess_or_target`
- `resample`: `cso`, `systematic`

## Map API

[`Geomag/algorithms.py`](Geomag/algorithms.py) exposes the public map entrypoint:

```python
from Geomag.algorithms import get_map
```

Two branches are currently supported:

- `source="uji"`: build a continuous map from UJIIndoorLoc-Mag
- `source="own"`: use a user-defined magnetic map, with direct matrix input preferred

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
