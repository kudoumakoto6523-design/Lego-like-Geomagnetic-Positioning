import logging
from Geomag.algorithms import get_map
from Geomag.models import RunContext

logger = logging.getLogger(__name__)


class Initializer:
    def __init__(
        self,
        num_runs=1,
        window_size=1000,
        route_source="uji",
        sensor_source="uji",
        data_root="data/raw",
        uji_test_file="tt01.txt",
        own_data_dir="data/Geomagnetic Navigation 2026-03-03 15-28-45",
        own_dataset_key=None,
        geomag_map=None,
        own_grid_array=None,
        own_grid_map_path=None,
        own_grid_format="array",
        own_grid_meta=None,
    ):
        self.num_runs = num_runs
        self.window_size = window_size
        self.route_source = route_source
        self.sensor_source = sensor_source
        self.data_root = data_root
        self.uji_test_file = uji_test_file
        self.own_data_dir = own_data_dir
        self.own_dataset_key = own_dataset_key
        self.geomag_map = geomag_map
        self.own_grid_array = own_grid_array
        self.own_grid_map_path = own_grid_map_path
        self.own_grid_format = own_grid_format
        self.own_grid_meta = own_grid_meta

    def create_context(self):
        if self.geomag_map is not None:
            geomag_map = self.geomag_map
        elif str(self.route_source).lower() == "own" or str(self.sensor_source).lower() == "own":
            geomag_map = get_map(
                source="own",
                own_grid_array=self.own_grid_array,
                own_grid_map_path=self.own_grid_map_path,
                own_grid_format=self.own_grid_format,
                own_grid_meta=self.own_grid_meta,
            )
        else:
            # Prefer building map; fallback to existing artifacts if optional deps are missing.
            try:
                geomag_map = get_map(source="uji", data_root=self.data_root)
            except Exception:
                logger.warning(
                    "UJI map building failed — falling back to pre-built artifacts. "
                    "Install pykrige and gstools for full map building support."
                )
                geomag_map = {
                    "source": "uji",
                    "output_model_npz": "data/processed/uji_mag_model_kriging.npz",
                    "output_preview_npz": "data/processed/uji_mag_grid_preview_kriging.npz",
                }
        return RunContext(
            num_runs=self.num_runs,
            window_size=self.window_size,
            geomag_map=geomag_map,
            route_source=self.route_source,
            sensor_source=self.sensor_source,
            data_root=self.data_root,
            uji_test_file=self.uji_test_file,
            own_data_dir=self.own_data_dir,
            own_dataset_key=self.own_dataset_key,
        )
