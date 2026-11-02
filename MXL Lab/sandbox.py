"""Sandbox de ejecución de código Python del usuario.

Capas de defensa:
1. Validación estática del AST (lista blanca de módulos, nombres y atributos).
2. Subproceso aislado (python -I) con entorno vacío de secretos, directorio
   temporal propio, builtins restringidos e importación filtrada.
3. Límites del sistema operativo: CPU, memoria, tamaño de archivo, descriptores
   y procesos hijos; más un timeout duro que mata todo el grupo de procesos.
"""
import ast
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time

try:
    import resource  # solo existe en Linux/macOS
except ImportError:  # pragma: no cover
    resource = None

TIMEOUT_SECONDS = 5
MAX_CODE_CHARS = 20_000
MAX_STDIN_CHARS = 10_000
MAX_OUTPUT_BYTES = 20_000
MEMORY_LIMIT_BYTES = 256 * 1024 * 1024

ALLOWED_MODULES = {
    "math", "random", "statistics", "itertools", "functools", "collections",
    "string", "re", "json", "datetime", "decimal", "fractions", "heapq",
    "bisect", "textwrap", "operator", "typing", "dataclasses", "enum",
}
FORBIDDEN_NAMES = {
    "eval", "exec", "compile", "open", "__import__", "globals", "locals",
    "vars", "getattr", "setattr", "delattr", "breakpoint", "memoryview",
    "help", "exit", "quit",
}
FORBIDDEN_ATTRS = {
    "gi_frame", "gi_code", "f_globals", "f_locals", "f_builtins", "f_back",
    "tb_frame", "cr_frame", "ag_frame", "func_globals", "mro",
}
ALLOWED_DUNDER_ATTRS = {"__name__", "__init__"}

# Programa que corre dentro del subproceso: aplica builtins restringidos.
RUNNER = r'''
import builtins, sys, traceback

ALLOWED = set(sys.argv[2].split(","))
NAMES = """abs all any bin bool bytes callable chr dict divmod enumerate filter
float format frozenset hash hex id input int isinstance issubclass iter len list
map max min next object oct ord pow print range repr reversed round set slice
sorted str sum super tuple type zip property staticmethod classmethod
__build_class__""".split()

safe = {n: getattr(builtins, n) for n in NAMES}
safe.update({k: v for k, v in vars(builtins).items()
             if isinstance(v, type) and issubclass(v, BaseException)})
real_import = builtins.__import__

def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
    if level != 0 or name.split(".")[0] not in ALLOWED:
        raise ImportError("Módulo no permitido en el sandbox: " + name)
    return real_import(name, globals, locals, fromlist, level)

safe["__import__"] = guarded_import

with open(sys.argv[1], encoding="utf-8") as fh:
    source = fh.read()

try:
    exec(compile(source, "<usuario>", "exec"),
         {"__builtins__": safe, "__name__": "__main__"})
except SystemExit:
    pass
except BaseException:
    kind, value, tb = sys.exc_info()
    traceback.print_exception(kind, value, tb.tb_next)  # oculta el marco del runner
    sys.exit(1)
'''


def validate_code(code):
    """Devuelve la lista de violaciones de seguridad (vacía si el código es válido)."""
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []  # el intérprete mostrará el error de sintaxis nativo
    problems = []
    for node in ast.walk(tree):
        line = getattr(node, "lineno", "?")
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] not in ALLOWED_MODULES:
                    problems.append(f"línea {line}: import no permitido '{alias.name}'")
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            if node.level or root not in ALLOWED_MODULES:
                problems.append(f"línea {line}: import no permitido '{node.module}'")
        elif isinstance(node, ast.Name) and node.id in FORBIDDEN_NAMES:
            problems.append(f"línea {line}: '{node.id}' está prohibido")
        elif isinstance(node, ast.Attribute):
            dunder = node.attr.startswith("__") and node.attr not in ALLOWED_DUNDER_ATTRS
            if dunder or node.attr in FORBIDDEN_ATTRS:
                problems.append(f"línea {line}: atributo '{node.attr}' está prohibido")
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            # Evita escapes tipo "{0.__class__}".format(x)
            if "__" in node.value and node.value != "__main__":
                problems.append(f"línea {line}: texto con '__' no permitido")
    return problems


def _apply_limits():
    """Se ejecuta en el hijo justo antes de lanzar Python (solo Linux/macOS)."""
    if resource is None:
        return
    resource.setrlimit(resource.RLIMIT_CPU, (TIMEOUT_SECONDS + 1,) * 2)
    resource.setrlimit(resource.RLIMIT_AS, (MEMORY_LIMIT_BYTES,) * 2)
    resource.setrlimit(resource.RLIMIT_FSIZE, (1_000_000,) * 2)
    resource.setrlimit(resource.RLIMIT_NOFILE, (32, 32))
    resource.setrlimit(resource.RLIMIT_NPROC, (0, 0))  # sin fork/subprocesos
    os.umask(0o077)


def _result(ok, stdout="", stderr="", exit_code=0, timed_out=False, ms=0, blocked=False):
    return {
        "ok": ok, "stdout": stdout, "stderr": stderr, "exit_code": exit_code,
        "timed_out": timed_out, "duration_ms": ms, "blocked": blocked,
    }


def _read_capped(path):
    with open(path, "rb") as fh:
        return fh.read(MAX_OUTPUT_BYTES).decode("utf-8", errors="replace")


def run_code(code, stdin_data=""):
    """Ejecuta `code` de forma aislada y devuelve stdout, stderr y metadatos."""
    if len(code) > MAX_CODE_CHARS:
        return _result(False, stderr=f"El código supera {MAX_CODE_CHARS} caracteres.", exit_code=-1, blocked=True)
    stdin_data = (stdin_data or "")[:MAX_STDIN_CHARS]

    problems = validate_code(code)
    if problems:
        msg = "Código bloqueado por seguridad:\n- " + "\n- ".join(problems[:10])
        return _result(False, stderr=msg, exit_code=-1, blocked=True)

    workdir = tempfile.mkdtemp(prefix="mxl_")
    try:
        runner_path = os.path.join(workdir, "runner.py")
        user_path = os.path.join(workdir, "user_code.py")
        out_path = os.path.join(workdir, "stdout.txt")
        err_path = os.path.join(workdir, "stderr.txt")
        with open(runner_path, "w", encoding="utf-8") as fh:
            fh.write(RUNNER)
        with open(user_path, "w", encoding="utf-8") as fh:
            fh.write(code)

        # Entorno mínimo: NUNCA se heredan DATABASE_URL, REDIS_URL ni MONGO_URL.
        env = {k: os.environ[k] for k in ("PATH", "LD_LIBRARY_PATH", "LANG") if k in os.environ}
        env["PYTHONIOENCODING"] = "utf-8"

        timed_out = False
        start = time.monotonic()
        with open(out_path, "wb") as fo, open(err_path, "wb") as fe:
            proc = subprocess.Popen(
                [sys.executable, "-I", "-B", runner_path, user_path, ",".join(sorted(ALLOWED_MODULES))],
                stdin=subprocess.PIPE, stdout=fo, stderr=fe, cwd=workdir, env=env,
                preexec_fn=_apply_limits, start_new_session=True,
            )
            try:
                proc.communicate(stdin_data.encode("utf-8"), timeout=TIMEOUT_SECONDS)
            except subprocess.TimeoutExpired:
                timed_out = True
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                proc.wait()
        ms = int((time.monotonic() - start) * 1000)

        stdout, stderr = _read_capped(out_path), _read_capped(err_path)
        if timed_out:
            stderr += f"\nTiempo límite excedido ({TIMEOUT_SECONDS} s)."
        elif proc.returncode and proc.returncode < 0:
            stderr += f"\nProceso terminado por la señal {-proc.returncode} (límite de recursos)."
        return _result(proc.returncode == 0 and not timed_out, stdout, stderr,
                       proc.returncode, timed_out, ms)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
