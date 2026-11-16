"""HOR-124: todo subprocesso do backend: sem shell, argv em lista, ambiente mínimo, prazo, saída limitada e árvore
encerrada (Job Object no Windows). Usa só processos Python fictícios em diretórios temporários."""
import ast
import asyncio
import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from horus_backend.processes import OUTPUT_LIMIT, SAFE_ENV_KEYS, TELEMETRY_OFF, OwnedProcess, minimal_env, run_sync

SRC = Path(__file__).resolve().parents[1] / "src" / "horus_backend"
PY = sys.executable

# Módulos que podem criar subprocessos. Um módulo novo precisa passar por OwnedProcess/run_sync ou ser justificado aqui.
#  processes.py        — implementação (OwnedProcess, run_sync)
#  production_worker.py, transcriber.py — executam DENTRO do worker, que já roda sob OwnedProcess (a árvore herda o Job)
#  local_pilot.py/…    — usam OwnedProcess
ALLOWED_RAW_SUBPROCESS = {"processes.py", "production_worker.py", "transcriber.py"}


def alive(pid):
    if os.name == "nt":
        import ctypes
        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        code = ctypes.c_ulong()
        ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        ctypes.windll.kernel32.CloseHandle(handle)
        return code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def wait_dead(pid, seconds=10):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if not alive(pid):
            return True
        time.sleep(0.1)
    return False


def wait_file(path: Path, seconds=10):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if path.exists() and path.read_text().strip():
            return path.read_text().strip()
        time.sleep(0.05)
    raise AssertionError(f"{path} não foi escrito")


TREE = textwrap.dedent("""
    import subprocess, sys, time
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    open(sys.argv[1], "w").write(str(child.pid))
    time.sleep(120)
""")


# --- inventário estático --------------------------------------------------------------------------------------

def test_no_shell_and_raw_subprocess_only_in_allowed_modules():
    offenders = []
    for path in SRC.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = ast.unparse(node.func)
            if name in {"os.system", "os.popen", "subprocess.getoutput", "subprocess.getstatusoutput",
                        "asyncio.create_subprocess_shell"} or name.endswith("create_subprocess_shell"):
                offenders.append(f"{path.name}: {name}")
            if any(k.arg == "shell" and not (isinstance(k.value, ast.Constant) and k.value.value is False) for k in node.keywords):
                offenders.append(f"{path.name}: shell=")
            if name in {"subprocess.run", "subprocess.Popen", "subprocess.call", "subprocess.check_call", "subprocess.check_output",
                        "asyncio.create_subprocess_exec"} and path.name not in ALLOWED_RAW_SUBPROCESS:
                offenders.append(f"{path.name}: {name} fora de processes.OwnedProcess/run_sync")
    assert not offenders, offenders


def test_raw_subprocess_modules_never_use_shell_or_string_commands():
    for name in ALLOWED_RAW_SUBPROCESS:
        for node in ast.walk(ast.parse((SRC / name).read_text(encoding="utf-8-sig"))):
            if isinstance(node, ast.Call) and ast.unparse(node.func) in {"subprocess.run", "subprocess.Popen", "subprocess.call"}:
                assert not any(k.arg == "shell" and getattr(k.value, "value", None) is not False for k in node.keywords)
                first = node.args[0] if node.args else None
                assert not isinstance(first, (ast.Constant, ast.JoinedStr)), f"{name}: comando como string"


# --- ambiente -------------------------------------------------------------------------------------------------

@pytest.fixture
def planted(monkeypatch):
    for key in ("HORUS_API_TOKEN", "AWS_SECRET_ACCESS_KEY", "OPENAI_API_KEY", "ELEVENLABS_API_KEY"):
        monkeypatch.setenv(key, "segredo-do-operador")


def env_dump():
    return [PY, "-I", "-c", "import os,json;print(json.dumps(sorted(os.environ)))"]


def test_owned_process_defaults_to_a_minimal_environment(planted):
    async def go():
        owned = await OwnedProcess.start(env_dump())
        out, _ = await owned.communicate(timeout=20)
        await owned.stop()
        owned.close()
        return out.decode()
    names = {n.upper() for n in __import__("json").loads(asyncio.run(go()))}
    assert not {"HORUS_API_TOKEN", "AWS_SECRET_ACCESS_KEY", "OPENAI_API_KEY", "ELEVENLABS_API_KEY"} & names
    assert names <= SAFE_ENV_KEYS | set(TELEMETRY_OFF) | {"PATHEXT", "COMSPEC", "SYSTEMDRIVE", "LC_CTYPE", "PYTHONIOENCODING", "__PYVENV_LAUNCHER__"}


def test_minimal_env_is_an_allowlist(planted):
    assert not {"HORUS_API_TOKEN", "AWS_SECRET_ACCESS_KEY"} & set(minimal_env())
    assert {k.upper() for k in minimal_env()} <= SAFE_ENV_KEYS


def test_run_sync_gives_a_minimal_environment_and_closed_stdin(planted):
    out = run_sync([PY, "-I", "-c", "import os,sys;print('HORUS_API_TOKEN' in os.environ, repr(sys.stdin.read()))"], timeout=20)
    assert out.decode().strip() == "False ''"


# --- sem shell ------------------------------------------------------------------------------------------------

def test_arguments_are_never_interpreted_by_a_shell(tmp_path):
    marker = tmp_path / "injetado.txt"
    hostile = f"x & echo pwned > \"{marker}\" ; echo $(whoami) | calc"
    out = run_sync([PY, "-I", "-c", "import sys;print(sys.argv[1])", hostile], timeout=20)
    assert out.decode().strip() == hostile and not marker.exists()


def test_missing_executable_is_an_oserror_not_a_shell_error():
    with pytest.raises(OSError):
        run_sync(["horus-programa-inexistente-xyz"], timeout=5)


# --- saída limitada -------------------------------------------------------------------------------------------

def test_run_sync_output_is_bounded():
    big = [PY, "-I", "-c", "import sys;sys.stdout.write('a'*3_000_000);sys.stderr.write('b'*3_000_000)"]
    assert len(run_sync(big, timeout=30, limit=1000)) == 1000
    failing = [PY, "-I", "-c", "import sys;sys.stderr.write('b'*3_000_000);sys.exit(3)"]
    with pytest.raises(subprocess.CalledProcessError) as error:
        run_sync(failing, timeout=30, limit=1000)
    assert len(error.value.stderr) == 1000 and error.value.returncode == 3


def test_owned_process_output_is_bounded_and_never_blocks_the_child():
    async def go():
        owned = await OwnedProcess.start([PY, "-I", "-c", "import sys;sys.stdout.write('a'*4_000_000);sys.stderr.write('b'*4_000_000)"])
        out, err = await owned.communicate(timeout=30, limit=2048)
        await owned.stop()
        owned.close()
        return out, err, owned.process.returncode
    out, err, code = asyncio.run(go())
    assert len(out) == 2048 and len(err) == 2048 and code == 0
    assert OUTPUT_LIMIT == 16 * 1024 * 1024


# --- prazo e árvore -------------------------------------------------------------------------------------------

def test_run_sync_timeout_kills_the_whole_tree(tmp_path):
    pidfile = tmp_path / "grandchild.pid"
    with pytest.raises(subprocess.TimeoutExpired):
        run_sync([PY, "-I", "-c", TREE, str(pidfile)], timeout=3)
    assert wait_dead(int(wait_file(pidfile))), "neto órfão após timeout"


def test_owned_process_timeout_then_stop_kills_the_whole_tree(tmp_path):
    pidfile = tmp_path / "grandchild.pid"

    async def go():
        owned = await OwnedProcess.start([PY, "-I", "-c", TREE, str(pidfile)])
        try:
            with pytest.raises(TimeoutError):
                await owned.communicate(timeout=2)
        finally:
            await owned.stop()
            owned.close()
        return owned.process.pid
    leader = asyncio.run(go())
    assert wait_dead(leader) and wait_dead(int(wait_file(pidfile))), "órfãos após encerrar a posse"


def test_cancelling_the_task_kills_the_whole_tree(tmp_path):
    pidfile = tmp_path / "grandchild.pid"

    async def go():
        owned = await OwnedProcess.start([PY, "-I", "-c", TREE, str(pidfile)])

        async def work():
            try:
                await owned.communicate(timeout=60)
            finally:
                await owned.stop()
                owned.close()
        task = asyncio.create_task(work())
        await asyncio.sleep(2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    asyncio.run(go())
    assert wait_dead(int(wait_file(pidfile)))


@pytest.mark.skipif(os.name != "nt", reason="KILL_ON_JOB_CLOSE é do Windows; no POSIX o grupo é encerrado explicitamente")
def test_backend_death_kills_the_children_through_the_job_object(tmp_path):
    """O 'backend' fictício inicia um filho com árvore e morre sem limpar nada; o Job Object encerra todos."""
    pidfile, leaderfile = tmp_path / "grandchild.pid", tmp_path / "leader.pid"
    backend = textwrap.dedent(f"""
        import asyncio, os, sys
        sys.path.insert(0, {str(SRC.parent)!r})
        from horus_backend.processes import OwnedProcess
        async def main():
            owned = await OwnedProcess.start([sys.executable, "-I", "-c", {TREE!r}, {str(pidfile)!r}])
            open({str(leaderfile)!r}, "w").write(str(owned.process.pid))
            while not os.path.exists({str(pidfile)!r}):
                await asyncio.sleep(0.05)
            os._exit(0)          # morte abrupta: nenhum finally/stop roda
        asyncio.run(main())
    """)
    subprocess.run([PY, "-c", backend], timeout=60, check=True)
    assert wait_dead(int(wait_file(leaderfile))) and wait_dead(int(wait_file(pidfile))), "filhos sobreviveram ao backend"


def test_minimal_environment_preserves_system_drive_without_secrets(monkeypatch):
    monkeypatch.setenv("SystemDrive", "C:")
    monkeypatch.setenv("HORUS_API_TOKEN", "segredo")
    env = minimal_env()
    assert {k.upper(): v for k, v in env.items()}["SYSTEMDRIVE"] == "C:"
    assert "HORUS_API_TOKEN" not in env


@pytest.mark.skipif(os.name != "nt", reason="Regressão do Shell.Application no Windows")
def test_bitlocker_query_does_not_create_literal_systemdrive_cache(tmp_path, monkeypatch):
    from horus_backend.disk_encryption import _query
    assert os.environ.get("SystemDrive")
    monkeypatch.chdir(tmp_path)
    _query(Path(sys.executable).drive)
    assert not (tmp_path / "%SystemDrive%").exists()
