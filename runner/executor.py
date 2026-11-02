"""Ejecutor multilenguaje. Corre SOLO en el servicio runner, nunca en la web.

Defensas: usuario sin privilegios (Dockerfile), directorio temporal por
ejecución, entorno sin secretos, límites de CPU/memoria/archivos/procesos,
timeout duro con kill del grupo de procesos y, si el sistema lo permite,
namespace de red vacío (RUNNER_NETWORK=off).
"""
import os
import shutil
import signal
import subprocess
import tempfile
import time

try:
    import resource  # solo Linux/macOS
except ImportError:  # pragma: no cover
    resource = None

RUN_TIMEOUT = int(os.getenv("RUNNER_TIMEOUT", "10"))
COMPILE_TIMEOUT = 20
MAX_OUTPUT_BYTES = 50_000
MEMORY_BYTES = 768 * 1024 * 1024
NETWORK_ON = os.getenv("RUNNER_NETWORK", "off") == "on"

SPECS = {
    "python": {"file": "main.py", "run": ["python3", "-B", "main.py"]},
    "javascript": {"file": "main.js", "run": ["node", "--max-old-space-size=256", "main.js"], "no_as": True},
    "c": {"file": "main.c", "compile": ["gcc", "-O1", "-Wall", "-o", "main", "main.c", "-lm"], "run": ["./main"]},
    "cpp": {"file": "main.cpp", "compile": ["g++", "-O1", "-Wall", "-std=c++17", "-o", "main", "main.cpp"], "run": ["./main"]},
    "bash": {"file": "main.sh", "run": ["bash", "main.sh"]},
    "ruby": {"file": "main.rb", "run": ["ruby", "main.rb"]},
    "php": {"file": "main.php", "run": ["php", "main.php"]},
}


def _unshare_works():
    try:
        return subprocess.run(["unshare", "-rn", "true"], capture_output=True, timeout=3).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


# True si el código de usuario corre sin interfaz de red.
NET_ISOLATED = (not NETWORK_ON) and _unshare_works()


def _limits(timeout, use_as):
    def apply():
        if resource is None:
            return
        resource.setrlimit(resource.RLIMIT_CPU, (timeout + 2,) * 2)
        if use_as:
            resource.setrlimit(resource.RLIMIT_AS, (MEMORY_BYTES,) * 2)
        resource.setrlimit(resource.RLIMIT_FSIZE, (5_000_000,) * 2)
        resource.setrlimit(resource.RLIMIT_NOFILE, (256, 256))
        resource.setrlimit(resource.RLIMIT_NPROC, (256, 256))
        os.umask(0o077)
    return apply


def _read(path):
    with open(path, "rb") as fh:
        return fh.read(MAX_OUTPUT_BYTES).decode("utf-8", errors="replace")


def _spawn(cmd, cwd, stdin_data, timeout, use_as):
    """Lanza un proceso con límites. Devuelve (código, stdout, stderr, timeout, ms)."""
    if NET_ISOLATED:
        cmd = ["unshare", "-rn"] + cmd
    env = {"PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"), "HOME": cwd,
           "TMPDIR": cwd, "LANG": "C.UTF-8", "PYTHONIOENCODING": "utf-8"}
    out_path, err_path = os.path.join(cwd, ".stdout"), os.path.join(cwd, ".stderr")
    timed_out = False
    start = time.monotonic()
    with open(out_path, "wb") as fo, open(err_path, "wb") as fe:
        try:
            proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=fo, stderr=fe, cwd=cwd, env=env,
                                    preexec_fn=_limits(timeout, use_as), start_new_session=True)
        except OSError as exc:
            return 127, "", f"No se pudo iniciar {cmd[0]}: {exc}", False, 0
        try:
            proc.communicate(stdin_data.encode("utf-8"), timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.wait()
    ms = int((time.monotonic() - start) * 1000)
    return proc.returncode, _read(out_path), _read(err_path), timed_out, ms


def _result(language, ok, stdout="", stderr="", code=0, timed_out=False, ms=0, blocked=False):
    return {"ok": ok, "stdout": stdout, "stderr": stderr, "exit_code": code, "timed_out": timed_out,
            "duration_ms": ms, "blocked": blocked, "language": language, "network_isolated": NET_ISOLATED}


def execute(language, source, stdin_data=""):
    spec = SPECS.get(language)
    if spec is None:
        return _result(language, False, stderr=f"Lenguaje no soportado: {language}", code=-1, blocked=True)
    workdir = tempfile.mkdtemp(prefix="mxl_")
    try:
        with open(os.path.join(workdir, spec["file"]), "w", encoding="utf-8") as fh:
            fh.write(source)
        use_as = not spec.get("no_as")
        if "compile" in spec:
            code, out, err, timed_out, ms = _spawn(spec["compile"], workdir, "", COMPILE_TIMEOUT, True)
            if code != 0 or timed_out:
                return _result(language, False, out, err or "Error de compilación.", code, timed_out, ms)
        code, out, err, timed_out, ms = _spawn(spec["run"], workdir, stdin_data, RUN_TIMEOUT, use_as)
        if timed_out:
            err += f"\nTiempo límite excedido ({RUN_TIMEOUT} s)."
        elif code < 0:
            err += f"\nProceso terminado por la señal {-code} (límite de recursos)."
        return _result(language, code == 0 and not timed_out, out, err, code, timed_out, ms)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
