from pathlib import Path

from Geomag.utils import collect_sensor_stream, get_uji_map

from Geomag.algorithms import get_sensor, get_test_len, get_true_route, visualize


def main():
    # 1) Sensor length/stream test: UJI
    uji_kwargs = {"uji_test_file": "tt01.txt"}
    uji_len = get_test_len(source="uji", **uji_kwargs)
    first_uji = get_sensor(source="uji", **uji_kwargs)
    print(f"UJI test length: {uji_len}")
    print(f"UJI first frame (mag, acc, gyro): {first_uji}")

    # Re-collect full stream for visualization.
    uji_sensor_data = collect_sensor_stream(source="uji", **uji_kwargs)
    route = get_true_route(source="uji", uji_test_file="tt01.txt")
    geomag_map = get_uji_map()

    # 2) Visualization test: combined horizontal layout (map + sensor panel)
    out_combined = visualize(
        mode="ujimap",
        geomag_map=geomag_map,
        route=route,
        sensor_data=uji_sensor_data,
        meta=["map", "sensor", "acc_x"],
        show=False,
    )
    print(f"Saved combined figure: {out_combined}")

    # 3) Visualization test: separate figures via separate calls
    out_map_only = visualize(
        mode="ujimap",
        geomag_map=geomag_map,
        meta=["map"],
        show=False,
    )
    out_sensor_only = visualize(
        mode="ujimap",
        geomag_map=geomag_map,
        route=route,
        sensor_data=uji_sensor_data,
        meta=["sensor", "acc_x"],
        show=False,
    )
    print("Saved separated figures:")
    print(f"  {out_map_only}")
    print(f"  {out_sensor_only}")

    # 4) Sensor length/stream test: own branch
    own_dir = "data/Geomagnetic Navigation 2026-03-03 15-28-45"
    own_len = get_test_len(source="own", own_data_dir=own_dir)
    first_own = get_sensor(source="own", own_data_dir=own_dir)
    print(f"OWN test length: {own_len}")
    print(f"OWN first frame (mag, acc, gyro): {first_own}")

    # Check output existence quickly.
    for p in [out_combined, out_map_only, out_sensor_only]:
        print(f"Exists {p}: {Path(p).exists()}")


if __name__ == "__main__":
    main()
