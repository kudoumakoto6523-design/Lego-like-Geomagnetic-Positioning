"""Run one outdoor navigation test against RTK ground truth."""

from Geomag.branching import BranchConfig, run_branch_simulation


if __name__ == "__main__":
    result = run_branch_simulation(
        BranchConfig(
            branch="outdoor",
            outdoor_navigation_key="nav1",
            show=False,
        )
    )
    print(result["pf_error_stats"])
