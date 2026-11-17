"""Fixtures locais; cada processo/worker possui seu próprio cluster."""
import pytest
from harness import Cluster


@pytest.fixture(scope="session")
def pg_cluster():
    with Cluster() as cluster:
        yield cluster


@pytest.fixture
def pg_pool(pg_cluster):
    with pg_cluster.database() as pool:
        yield pool
