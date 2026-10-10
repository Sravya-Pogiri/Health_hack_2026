import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engine.config import load_config  # noqa: E402
from engine.dataset import Dataset  # noqa: E402
from engine.timeline import replay  # noqa: E402

KIT_DIR = ROOT / "data" / "kit"


@pytest.fixture(scope="session")
def cfg():
    return load_config()


@pytest.fixture()
def dataset(cfg):
    return Dataset(cfg, KIT_DIR)


@pytest.fixture()
def world(dataset):
    return replay(dataset, dataset.default_now())
