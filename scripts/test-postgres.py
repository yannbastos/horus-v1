"""Entrada independente: suíte PG ou benchmark sintético, sem configuração do app."""
import argparse
import json
from pathlib import Path
import statistics
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend" / "tests" / "pg"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmark", type=int, metavar="N")
    args, pytest_args = parser.parse_known_args()
    if args.benchmark is None:
        return subprocess.call([sys.executable, "-m", "pytest",
            str(ROOT / "backend/tests/pg"), *pytest_args], cwd=ROOT)
    if args.benchmark < 1:
        parser.error("N deve ser positivo")
    from harness import Cluster
    import psycopg
    import psycopg_pool
    start = time.perf_counter()
    with Cluster() as cluster:
        for _ in range(args.benchmark):
            with cluster.database() as pool, pool.connection() as conn:
                assert conn.execute("INSERT INTO horus.event(key,payload) VALUES ('bench','{}') RETURNING id").fetchone() == (1,)
        result = dict(cluster.timings, postgres=cluster.version,
            psycopg=psycopg.__version__, psycopg_pool=psycopg_pool.__version__,
            cases=args.benchmark, elapsed_before_stop_seconds=time.perf_counter() - start)
        clones = result.pop("clone_seconds")
        result["clone_mean_seconds"] = statistics.mean(clones)
        result["clone_max_seconds"] = max(clones)
        result["projected_2900_seconds"] = (result["elapsed_before_stop_seconds"]
            - result["initdb_seconds"] - result["start_seconds"] - result["ddl_seconds"]) / args.benchmark * 2900
    result["elapsed_with_cleanup_seconds"] = time.perf_counter() - start
    result["removed"] = not cluster.root.exists()
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
