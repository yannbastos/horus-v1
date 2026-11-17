"""Fixtures locais; cada processo/worker possui seu próprio cluster."""
import pytest
from harness import Cluster


def pytest_configure(config):
    config.addinivalue_line("markers", "pg_strong: clone exclusivo por teste (API/threads/subprocessos/conexões concorrentes)")


def pytest_addoption(parser):
    parser.addoption("--pg-benchmark-cases", type=int, default=0)


def pytest_generate_tests(metafunc):
    if "pg_benchmark_case" in metafunc.fixturenames:
        metafunc.parametrize("pg_benchmark_case", range(metafunc.config.getoption("--pg-benchmark-cases")))


def pytest_collection_modifyitems(config, items):
    if not config.getoption("--pg-benchmark-cases"):
        items[:] = [item for item in items if "pg_benchmark_case" not in item.fixturenames]


@pytest.fixture(scope="session")
def pg_cluster():
    with Cluster() as cluster:
        yield cluster


@pytest.fixture(scope="session")
def _pg_worker_database(pg_cluster):
    with pg_cluster.database() as pool:
        with pool.connection() as conn:
            name = conn.info.dbname
        with pg_cluster.connect(name, autocommit=True) as admin:
            yield pool, admin


@pytest.fixture
def pg_pool(request, pg_cluster):
    if request.node.get_closest_marker("pg_strong"):
        with pg_cluster.database() as pool:
            yield pool
    else:
        pool, admin = request.getfixturevalue("_pg_worker_database")
        try:
            yield pool
        finally:
            pg_cluster.reset(admin, pool)
