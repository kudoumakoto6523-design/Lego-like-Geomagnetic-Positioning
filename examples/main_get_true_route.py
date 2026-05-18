from Geomag.algorithms import get_map, get_true_route, visualize


def main():
    geomag_map = get_map(source="uji")
    route = get_true_route(source="uji", uji_test_file="tt01.txt")

    overlay_png = "data/processed/uji_map_with_true_route.png"
    route_only_png = "data/processed/uji_true_route_only.png"

    visualize(
        geomag_map=geomag_map,
        route=route,
        mode="ujimap",
        meta=["map", "true_route"],
        show=False,
        output_png=overlay_png,
    )
    visualize(
        route=route,
        mode="ujimap",
        meta=["true_route"],
        show=False,
        output_png=route_only_png,
    )

    print(f"True route points: {len(route)}")
    print(f"Saved: {overlay_png}")
    print(f"Saved: {route_only_png}")


if __name__ == "__main__":
    main()
