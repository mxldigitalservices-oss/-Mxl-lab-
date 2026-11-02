"""Rutas del laboratorio: lenguajes, terminal simulada y probador HTTP seguro."""
import json

from flask import g, jsonify, request

import database as db
import http_safe
import terminal_sim
from languages import LANGUAGES

TERM_TTL = 60 * 60 * 24
TERM_POINTS = 30


def register(app, login_required, rate_limit):
    """Registra las rutas en `app` reutilizando los helpers de autenticación."""

    def load(uid):
        raw = db.get_redis().get(f"term:{uid}")
        return json.loads(raw) if raw else terminal_sim.initial_state()

    def save(uid, state):
        db.get_redis().setex(f"term:{uid}", TERM_TTL, json.dumps(state))

    def view(state, output="", edit=None, completed=None, **extra):
        return jsonify(output=output, prompt=terminal_sim.prompt(state), goals=terminal_sim.goals(state),
                       edit=edit, completed=completed, service=state["service"], **extra)

    def maybe_complete(uid, state):
        """Registra el escenario como completado en PostgreSQL (una sola vez)."""
        if state.get("completed") or not all(goal["done"] for goal in terminal_sim.goals(state)):
            return None
        state["completed"] = True
        final, points = terminal_sim.score(state), 0
        with db.pg_cursor() as cur:
            cur.execute(
                "INSERT INTO terminal_completions (user_id, scenario, score) VALUES (%s,%s,%s) "
                "ON CONFLICT (user_id, scenario) DO NOTHING RETURNING id", (uid, terminal_sim.SCENARIO, final))
            if cur.fetchone():
                points = TERM_POINTS
                cur.execute("UPDATE users SET points = points + %s WHERE id=%s", (points, uid))
        return {"score": final, "points": points}

    @app.get("/api/languages")
    def languages():
        return jsonify(languages=LANGUAGES)

    @app.get("/api/term/state")
    @login_required
    def term_state():
        state = load(g.user["id"])
        return view(state, guide=terminal_sim.GUIDE, help=terminal_sim.HELP)

    @app.post("/api/term")
    @login_required
    def term_exec():
        uid = g.user["id"]
        if not rate_limit(f"rl:term:{uid}", 120, 60):
            return jsonify(error="Demasiados comandos por minuto."), 429
        cmd = (request.get_json(silent=True) or {}).get("cmd", "")
        if not isinstance(cmd, str) or len(cmd) > 500:
            return jsonify(error="Comando inválido."), 400
        state = load(uid)
        shell = terminal_sim.Shell(state)
        output = shell.execute(cmd)
        completed = maybe_complete(uid, state)
        save(uid, state)
        return view(state, output, shell.edit, completed)

    @app.post("/api/term/write")
    @login_required
    def term_write():
        uid = g.user["id"]
        content = (request.get_json(silent=True) or {}).get("content")
        if not isinstance(content, str):
            return jsonify(error="Contenido inválido."), 400
        state = load(uid)
        shell = terminal_sim.Shell(state)
        try:
            output = shell.save_edit(content)
        except terminal_sim.ShellError as exc:
            return jsonify(error=str(exc)), 403
        completed = maybe_complete(uid, state)
        save(uid, state)
        return view(state, "\n".join([output] + shell.notes), None, completed)

    @app.post("/api/term/reset")
    @login_required
    def term_reset():
        state = terminal_sim.initial_state()
        save(g.user["id"], state)
        return view(state, "Entorno reiniciado.")

    @app.post("/api/http")
    @login_required
    def http_proxy():
        if not rate_limit(f"rl:http:{g.user['id']}", 30, 60):
            return jsonify(error="Límite de 30 peticiones por minuto."), 429
        data = request.get_json(silent=True) or {}
        headers, body = data.get("headers") or {}, data.get("body")
        if (not isinstance(headers, dict) or len(headers) > 20 or not isinstance(data.get("url"), str)
                or (body is not None and (not isinstance(body, str) or len(body) > 20_000))):
            return jsonify(error="Petición inválida."), 400
        try:
            return jsonify(http_safe.fetch(data.get("method", "GET"), data["url"], headers, body))
        except http_safe.HttpBlocked as exc:
            return jsonify(error=str(exc)), 400
