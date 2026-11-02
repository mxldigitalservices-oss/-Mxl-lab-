"""Cliente del servicio runner.

La web NUNCA ejecuta código de usuarios: lo envía al servicio `runner`, que
vive aparte y no tiene credenciales de bases de datos. Solo en desarrollo local
(ALLOW_LOCAL_EXEC=1) se ejecuta en este mismo proceso.
"""
import os

import requests

from languages import SERVER_LANGUAGES

MAX_CODE_CHARS = 100_000
MAX_STDIN_CHARS = 10_000


def _error(message, blocked=False):
    return {"ok": False, "stdout": "", "stderr": message, "exit_code": -1,
            "timed_out": False, "duration_ms": 0, "blocked": blocked}


def run_code(language, code, stdin_data=""):
    """Ejecuta `code` en `language` y devuelve stdout, stderr y metadatos."""
    if language not in SERVER_LANGUAGES:
        return _error(f"Lenguaje no soportado en el servidor: {language}", blocked=True)
    if len(code) > MAX_CODE_CHARS:
        return _error(f"El código supera {MAX_CODE_CHARS} caracteres.", blocked=True)
    stdin_data = (stdin_data or "")[:MAX_STDIN_CHARS]

    runner_url = os.getenv("RUNNER_URL", "").rstrip("/")
    if runner_url:
        try:
            response = requests.post(
                f"{runner_url}/exec",
                json={"language": language, "code": code, "stdin": stdin_data},
                headers={"Authorization": f"Bearer {os.environ['RUNNER_TOKEN']}"},
                timeout=45,
            )
            if response.status_code == 503:
                return _error("El runner está ocupado. Intenta de nuevo en unos segundos.")
            response.raise_for_status()
            return response.json()
        except (requests.RequestException, KeyError, ValueError):
            return _error("El servicio de ejecución no está disponible.")

    if os.getenv("ALLOW_LOCAL_EXEC") == "1":  # solo para desarrollo en tu equipo
        from runner.executor import execute
        return execute(language, code, stdin_data)

    return _error("El servicio de ejecución no está configurado (falta RUNNER_URL).")
