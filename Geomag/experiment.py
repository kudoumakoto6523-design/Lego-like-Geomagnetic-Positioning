from Geomag.pipeline import (
    GeomagPipeline,
    PDRConfig,
    PFConfig,
    build_pdr_from_config,
    build_pf_from_config,
)


class Experiment:
    def __init__(self, context, pdr_module=None, pf_module=None, pdr_config=None, pf_config=None):
        self.context = context
        self.pdr_config = pdr_config or PDRConfig()
        self.pf_config = pf_config or PFConfig()
        self.pdr_module = pdr_module or build_pdr_from_config(self.pdr_config)
        self.pf_module = pf_module or build_pf_from_config(self.pf_config)

    @staticmethod
    def describe_api():
        return GeomagPipeline.describe_configs()

    def run(self, **kwargs):
        pipeline = GeomagPipeline(
            self.context,
            pdr_module=self.pdr_module,
            pf_module=self.pf_module,
        )
        return pipeline.run(**kwargs)
