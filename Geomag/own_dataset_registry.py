from pathlib import Path


TILE_COUNT_X = 12
TILE_COUNT_Y = 8
TILE_SIZE_X_M = 0.96
TILE_SIZE_Y_M = 1.10


def tile_center(ix, iy):
    return [float((ix + 0.5) * TILE_SIZE_X_M), float((iy + 0.5) * TILE_SIZE_Y_M)]


ROUTE1_XY_M = [
    tile_center(1, 0),
    tile_center(1, 7),
    tile_center(5, 7),
    tile_center(5, 0),
    tile_center(1, 0),
]

ROUTE2_XY_M = [
    tile_center(1, 0),
    tile_center(1, 5),
    tile_center(4, 5),
]


_REPO_ROOT = Path(__file__).resolve().parent.parent
_OWN_PACKAGE_ROOT = _REPO_ROOT / "data" / "own_data_package"

_REGISTRY = {
    "route1_run1": {
        "folder_name": "route1_run1",
        "route_label": "route1",
        "route_xy_m": ROUTE1_XY_M,
        "assignment_confidence": "medium",
    },
    "route2_run1": {
        "folder_name": "route2_run1",
        "route_label": "route2",
        "route_xy_m": ROUTE2_XY_M,
        "assignment_confidence": "high",
    },
    "route1_run2": {
        "folder_name": "route1_run2",
        "route_label": "route1",
        "route_xy_m": ROUTE1_XY_M,
        "assignment_confidence": "high",
    },
}


def available_own_dataset_keys():
    return sorted(_REGISTRY.keys())


def get_own_dataset_spec(own_dataset_key):
    token = str(own_dataset_key or "").strip()
    if token not in _REGISTRY:
        raise ValueError(
            f"Unknown own dataset key: {own_dataset_key}. Available keys: {available_own_dataset_keys()}"
        )

    spec = dict(_REGISTRY[token])
    dataset_dir = (_OWN_PACKAGE_ROOT / spec["folder_name"]).resolve()
    return {
        "key": token,
        "dataset_dir": str(dataset_dir),
        "route_label": spec["route_label"],
        "route_xy_m": [[float(x), float(y)] for x, y in spec["route_xy_m"]],
        "assignment_confidence": spec["assignment_confidence"],
    }


def resolve_own_dataset_dir(own_dataset_key):
    return get_own_dataset_spec(own_dataset_key)["dataset_dir"]


def get_own_route_xy_m(own_dataset_key):
    return get_own_dataset_spec(own_dataset_key)["route_xy_m"]
