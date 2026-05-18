# Own Data Package

This package normalizes three raw ZIP captures into a deterministic layout:

- `route1_run1/` from `Geomagnetic Navigation 2026-03-12 21-12-10.zip`
- `route1_run2/` from `Geomagnetic Navigation 2026-03-19 20-04-39.zip`
- `route2_run1/` from `Geomagnetic Navigation 2026-03-19 20-03-26.zip`

Each route folder contains:

- `Accelerometer.csv`
- `Gyroscope.csv`
- `Magnetometer.csv`
- `Location.csv`
- `meta/device.csv`
- `meta/time.csv`

Map interpretation metadata is recorded in `manifest.json`:

- tile count: `12 x 8`
- tile size: `96 cm x 110 cm`
- coordinate model hint: tile-based, center anchor, `flip_y = true`

Warning: `route1_run1` is currently an assumed route assignment (`assignment_confidence = "medium"`).
