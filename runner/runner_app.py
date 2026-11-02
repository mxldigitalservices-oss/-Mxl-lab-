"""Servicio runner: recibe código autenticado y lo ejecuta de forma aislada.

Debe desplegarse como servicio separado (idealmente en otro proyecto de Railway)
y NO debe tener variables de base de datos.
"""
import hmac
import os
import threading

from flask import Flask, jsonify, request

from languages import SERVER_LANGUAGES
from runner.executor import NET_ISOLATED, execute

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 256 * 1024
TOKEN = os.environ["RUNNER_TOKEN"]
slots = threading.BoundedSemaphore(int(os.getenv("RUNNER_CONCURRENCY", "4")))


@app.get("/health")
def health():
    return jsonify(ok=True, network_isolated=NET_ISOLATED, languages=sorted(SERVER_LANGUAGES))


@app.post("/exec")
def run():
    supplied = request.headers.get("Authorization", "").removeprefix("Bearer ")
    if not hmac.compare_digest(supplied, TOKEN):
        return jsonify(error="No autorizado."), 401
    data = request.get_json(silent=True) or {}
    language, code, stdin = data.get("language"), data.get("code"), data.get("stdin", "")
    if language not in SERVER_LANGUAGES or not isinstance(code, str) or not isinstance(stdin, str):
        return jsonify(error="Petición inválida."), 400
    if not slots.acquire(blocking=False):  # protege la CPU del runner
        return jsonify(error="Runner ocupado."), 503
    try:
        return jsonify(execute(language, code, stdin))
    finally:
        slots.release()
