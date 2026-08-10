"""DeepSeek-driven outdoor tuning; reads the key from DEEPSEEK_API_KEY."""

from Geomag.branching import BranchConfig, run_branch_simulation


if __name__ == "__main__":
    result = run_branch_simulation(
        BranchConfig(
            branch="outdoor",
            outdoor_navigation_key="nav4",
            outdoor_auto_tune=True,
            outdoor_tune_iterations=3,
            show=False,
        )
    )
    print(result["tuning_best_trial"])
    print(result["pf_error_stats"])
