import pytest

from bakery_world.history import generate_history


@pytest.fixture(scope="session")
def seed1_history(tmp_path_factory):
    return generate_history(1, tmp_path_factory.mktemp("seed1-history"))
