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
    parser.add_argument("--strategy", choices=["fast", "wal_log", "file_copy", "pytest"], default="fast")
    parser.add_argument("--workers", type=int, default=0, help="Somente --strategy pytest; 0=sequencial")
    parser.add_argument("--untuned", action="store_true", help="Comparar durabilidade padrão do PostgreSQL")
    args, pytest_args = parser.parse_known_args()
    if args.benchmark is None:
        return subprocess.call([sys.executable, "-m", "pytest",
            str(ROOT / "backend/tests/pg"), *pytest_args], cwd=ROOT)
    if args.benchmark < 1:
        parser.error("N deve ser positivo")
    if args.workers < 0:
        parser.error("workers deve ser >=0")
    if args.strategy == "pytest":
        if args.untuned:
            parser.error("--untuned se aplica apenas ao benchmark direto")
        command = [sys.executable, "-m", "pytest", str(ROOT / "backend/tests/pg"),
            "-q", "-k", "test_benchmark_cycle", "--pg-benchmark-cases", str(args.benchmark)]
        if args.workers:
            command.extend(["-n", str(args.workers), "--dist", "load"])
        start = time.perf_counter()
        result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
        elapsed = time.perf_counter() - start
        print(json.dumps(dict(strategy="pytest", workers=args.workers or 1,
            cases=args.benchmark, elapsed_seconds=elapsed,
            seconds_per_case=elapsed / args.benchmark,
            projected_2900_seconds=elapsed / args.benchmark * 2900,
            returncode=result.returncode, pytest_output=result.stdout, stderr=result.stderr), indent=2))
        return result.returncode
    if args.workers:
        parser.error("--workers exige --strategy pytest")
    from harness import Cluster
    import psycopg
    import psycopg_pool
    start = time.perf_counter()
    with Cluster(tuned=not args.untuned) as cluster:
        cycles_start = time.perf_counter()
        resets = []
        if args.strategy == "fast":
            with cluster.database() as pool:
                with pool.connection() as conn:
                    name = conn.info.dbname
                with cluster.connect(name, autocommit=True) as admin:
                    for _ in range(args.benchmark):
                        with pool.connection() as conn:
                            assert conn.execute("SELECT * FROM horus.parent").fetchall() == [(1,)]
                            assert conn.execute("INSERT INTO horus.event(key,payload,parent_id) VALUES ('bench','{}',1) RETURNING id").fetchone() == (1,)
                        reset_start = time.perf_counter()
                        cluster.reset(admin, pool)
                        resets.append(time.perf_counter() - reset_start)
        else:
            for _ in range(args.benchmark):
                with cluster.database(strategy=args.strategy) as pool, pool.connection() as conn:
                    assert conn.execute("SELECT * FROM horus.parent").fetchall() == [(1,)]
                    assert conn.execute("INSERT INTO horus.event(key,payload,parent_id) VALUES ('bench','{}',1) RETURNING id").fetchone() == (1,)
        cycles_seconds = time.perf_counter() - cycles_start
        result = dict(cluster.timings, postgres=cluster.version,
            psycopg=psycopg.__version__, psycopg_pool=psycopg_pool.__version__,
            cases=args.benchmark, strategy=args.strategy, tuned=not args.untuned,
            cycles_seconds=cycles_seconds, seconds_per_case=cycles_seconds / args.benchmark,
            elapsed_before_stop_seconds=time.perf_counter() - start)
        clones = result.pop("clone_seconds")
        result["clone_mean_seconds"] = statistics.mean(clones)
        result["clone_max_seconds"] = max(clones)
        result["projected_2900_seconds"] = cycles_seconds / args.benchmark * 2900
        if resets:
            result["reset_mean_seconds"] = statistics.mean(resets)
    result["elapsed_with_cleanup_seconds"] = time.perf_counter() - start
    result["removed"] = not cluster.root.exists()
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
