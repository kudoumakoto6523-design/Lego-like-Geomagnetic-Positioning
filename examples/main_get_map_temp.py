import json

from Geomag.algorithms import get_map, visualize


def main():
    try:
        uji_map = get_map(source="uji")
        print(json.dumps(uji_map, indent=2))
        visualize(geomag_map=uji_map, mode="ujimap")
    except Exception as exc:
        print(f"UJI map test skipped/failed: {exc}")

    own_grid_array = [
        [45.10, 45.22, 45.31, 45.18],
        [44.97, 45.05, 45.27, 45.40],
        [44.83, 44.96, 45.14, 45.29],
        [44.72, 44.88, 45.02, 45.16],
    ]
    own_map = get_map(
        source="own",
        own_grid_array=own_grid_array,
        own_grid_meta={"cell_size_m": 0.5, "origin_xy_m": [0.0, 0.0]},
    )
    print(json.dumps(own_map, indent=2))
    visualize(geomag_map=own_map, mode="usermap")


if __name__ == "__main__":
    main()
