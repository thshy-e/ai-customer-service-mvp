from pathlib import Path

import pytest

from app.business import BusinessHours
from app.catalog import Catalog


@pytest.fixture
def config_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "config"


@pytest.fixture
def catalog(config_dir: Path) -> Catalog:
    return Catalog.from_directory(config_dir)


@pytest.fixture
def business_hours(config_dir: Path) -> BusinessHours:
    return BusinessHours.from_file(config_dir / "business-hours.yaml")

