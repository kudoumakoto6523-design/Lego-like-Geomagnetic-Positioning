"""Build the outdoor RTK geomagnetic map (no test route is loaded)."""

from Geomag.algorithms import get_map


if __name__ == "__main__":
    geomag_map = get_map(source="outdoor", outdoor_force_rebuild=True)
    print(f"source: {geomag_map['source']}")
    print(f"sessions: {geomag_map['map_sessions']}")
    print(f"usable samples: {geomag_map['map_usable_samples']}")
    print(f"spatial cells: {geomag_map['spatial_cells']}")
    print(f"grid shape: {geomag_map['grid_shape']}")
    print(f"map image: {geomag_map['output_png']}")
