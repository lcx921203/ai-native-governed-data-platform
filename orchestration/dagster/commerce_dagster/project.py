"""Canonical paths and dbt project handle for the Dagster code location."""

from pathlib import Path

from dagster_dbt import DbtProject


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DBT_PROJECT_DIR = PROJECT_ROOT / "dbt" / "mercaso_dbt"
DBT_PROFILES_DIR = DBT_PROJECT_DIR

commerce_dbt_project = DbtProject(
    project_dir=DBT_PROJECT_DIR,
    profiles_dir=DBT_PROFILES_DIR,
)
commerce_dbt_project.prepare_if_dev()
