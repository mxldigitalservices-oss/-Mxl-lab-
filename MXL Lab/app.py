"""MXL Lab: servidor Flask de práctica de programación y auditoría.

PostgreSQL -> usuarios, historial de lecciones, retos y calificaciones.
Redis      -> sesiones, límites de uso, contadores de intentos, caché y estado.
MongoDB    -> informes detallados del auditor y trazas de código.
"""
import json
import functools
import logging
import re
import secrets
from datetime import datetime, timezone

from bson import ObjectId
from dotenv import load_dotenv
from flask import Flask, g, jsonify, render_template, request
from psycopg2 import errors as pg_errors
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash, generate_password_hash

load_dotenv()

import database as db  # noqa: E402  (se importa tras cargar .env)
from auditor import audit_code  # noqa: E402
from sandbox import run_code  # noqa: E402

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 64 * 1024  # tope de 64 KB por petición
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)  # Railway

SESSION_COOKIE = "mxl_session"
SESSION_TTL = 60 * 60 * 24      # 24 h, renovable con cada uso
CACHE_TTL = 300                 # 5 min
RUN_LIMIT, RUN_WINDOW = 20, 60  # 20 ejecuciones por minuto y usuario
LOGIN_LIMIT, LOGIN_WINDOW = 10, 300
USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{3,32}$")

db.init_all()


# ------------------------------------------------------------------ Utilidades
def rate_limit(key, limit, window):
    """Contador en Redis: devuelve True si aún no se superó el límite."""
    r = db.get_redis()
    count = r.incr(key)
    if count == 1:
        r.expire(key, window)
    return count <= limit


def current_user():
    """Lee la sesión desde Redis (o None) y renueva su caducidad."""
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    r = db.get_redis()
    raw = r.get(f"sess:{token}")
    if not raw:
        return None
    r.expire(f"sess:{token}", SESSION_TTL)
    return json.loads(raw)


def login_required(view):
    @functools.wraps(view)
    def wrapper(*args, **kwargs):
        user = current_user()
        if not user:
            return jsonify(error="Inicia sesión para continuar."), 401
        g.user = user
        return view(*args, **kwargs)
    return wrapper


def start_session(response, user_id, username):
    """Crea la sesión en Redis y la cookie httpOnly."""
    token = secrets.token_urlsafe(32)
    db.get_redis().setex(f"sess:{token}", SESSION_TTL,
                         json.dumps({"id": user_id, "username": username}))
    response.set_cookie(SESSION_COOKIE, token, max_age=SESSION_TTL, httponly=True,
                        samesite="Lax", secure=request.is_secure)
    return response


def update_state(user_id, status, score):
    """Estado en tiempo real del usuario (última ejecución, puntuación, total)."""
    r = db.get_redis()
    key = f"state:{user_id}"
    pipe = r.pipeline()
    pipe.hset(key, mapping={"last_status": status, "last_score": score,
                            "last_run_at": datetime.now(timezone.utc).isoformat()})
    pipe.hincrby(key, "runs", 1)
    pipe.expire(key, SESSION_TTL)
    pipe.execute()


def cached_rows(key, sql):
    """Consulta de solo lectura con caché en Redis."""
    r = db.get_redis()
    hit = r.get(key)
    if hit:
        return json.loads(hit)
    with db.pg_cursor() as cur:
        cur.execute(sql)
        rows = cur.fetchall()
    r.setex(key, CACHE_TTL, json.dumps(rows))
    return rows


def clean_doc(doc):
    """Convierte ObjectId y datetime de Mongo a tipos serializables en JSON."""
    out = {}
    for key, value in doc.items():
        if isinstance(value, ObjectId):
            value = str(value)
        elif isinstance(value, datetime):
            value = value.isoformat()
        out[key] = value
    return out


def persist_run(user_id, kind, code, stdin, result, report, lesson_id=None, challenge_id=None):
    """Guarda traza e informe en Mongo y calificación/historial en PostgreSQL."""
    mongo = db.get_mongo()
    now = datetime.now(timezone.utc)
    trace_id = mongo.code_traces.insert_one({
        "user_id": user_id, "kind": kind, "code": code, "stdin": stdin,
        "stdout": result["stdout"], "stderr": result["stderr"],
        "exit_code": result["exit_code"], "duration_ms": result["duration_ms"],
        "created_at": now,
    }).inserted_id
    report_id = mongo.audit_reports.insert_one({
        "user_id": user_id, "trace_id": trace_id, "kind": kind,
        "created_at": now, **report,
    }).inserted_id
    with db.pg_cursor() as cur:
        if lesson_id:
            cur.execute(
                "INSERT INTO lesson_history (user_id, lesson_id, success, run_ms) VALUES (%s,%s,%s,%s)",
                (user_id, lesson_id, result["ok"], result["duration_ms"]))
        cur.execute(
            "INSERT INTO auditor_grades (user_id, lesson_id, challenge_id, score, grade, mongo_report_id) "
            "VALUES (%s,%s,%s,%s,%s,%s)",
            (user_id, lesson_id, challenge_id, report["score"], report["grade"], str(report_id)))
    return str(report_id)


def safe_persist(*args, **kwargs):
    """Un fallo de persistencia no debe ocultar el resultado de la ejecución."""
    try:
        return persist_run(*args, **kwargs)
    except Exception:
        app.logger.exception("No se pudo guardar la ejecución")
        return None


def normalize(text):
    return "\n".join(line.rstrip() for line in text.strip().splitlines())


# ------------------------------------------------------------------------ Vistas
@app.get("/")
def index():
    return render_template("index.html")


@app.get("/health")
def health():
    status = db.health()
    ok = all(v == "ok" for v in status.values())
    return jsonify(status=status, ok=ok), (200 if ok else 503)


# --------------------------------------------------------------------------- Auth
@app.post("/api/auth/register")
def register():
    data = request.get_json(silent=True) or {}
    username, password = data.get("username", ""), data.get("password", "")
    if not isinstance(username, str) or not USERNAME_RE.match(username):
        return jsonify(error="El usuario debe tener 3-32 letras, números o guion bajo."), 400
    if not isinstance(password, str) or len(password) < 8:
        return jsonify(error="La contraseña debe tener al menos 8 caracteres."), 400
    try:
        with db.pg_cursor() as cur:
            cur.execute("INSERT INTO users (username, password_hash) VALUES (%s,%s) RETURNING id",
                        (username, generate_password_hash(password)))
            user_id = cur.fetchone()["id"]
    except pg_errors.UniqueViolation:
        return jsonify(error="Ese usuario ya existe."), 409
    return start_session(jsonify(user={"username": username}), user_id, username), 201


@app.post("/api/auth/login")
def login():
    if not rate_limit(f"rl:login:{request.remote_addr}", LOGIN_LIMIT, LOGIN_WINDOW):
        return jsonify(error="Demasiados intentos. Espera unos minutos."), 429
    data = request.get_json(silent=True) or {}
    username, password = data.get("username", ""), data.get("password", "")
    with db.pg_cursor() as cur:
        cur.execute("SELECT id, password_hash FROM users WHERE username=%s",
                    (username if isinstance(username, str) else "",))
        row = cur.fetchone()
    if not row or not isinstance(password, str) or not check_password_hash(row["password_hash"], password):
        return jsonify(error="Usuario o contraseña incorrectos."), 401
    return start_session(jsonify(user={"username": username}), row["id"], username)


@app.post("/api/auth/logout")
def logout():
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        db.get_redis().delete(f"sess:{token}")
    response = jsonify(ok=True)
    response.delete_cookie(SESSION_COOKIE)
    return response


@app.get("/api/me")
@login_required
def me():
    with db.pg_cursor() as cur:
        cur.execute("SELECT username, points FROM users WHERE id=%s", (g.user["id"],))
        row = cur.fetchone()
    if not row:
        return jsonify(error="Usuario no encontrado."), 401
    return jsonify(user={**row, "state": db.get_redis().hgetall(f"state:{g.user['id']}")})


# ------------------------------------------------------------------ Contenido
@app.get("/api/lessons")
def lessons():
    rows = cached_rows("cache:lessons",
                       "SELECT slug, title, content, starter_code FROM lessons ORDER BY position")
    return jsonify(lessons=rows)


@app.get("/api/challenges")
def challenges():
    rows = cached_rows("cache:challenges",
                       "SELECT id, slug, title, description, starter_code, points FROM challenges ORDER BY id")
    done = set()
    user = current_user()
    if user:
        with db.pg_cursor() as cur:
            cur.execute("SELECT challenge_id FROM challenge_completions WHERE user_id=%s", (user["id"],))
            done = {r["challenge_id"] for r in cur.fetchall()}
    return jsonify(challenges=[{**r, "completed": r["id"] in done} for r in rows])


# ---------------------------------------------------------------------- Ejecución
@app.post("/api/run")
@login_required
def run():
    data = request.get_json(silent=True) or {}
    code, stdin, lesson_slug = data.get("code", ""), data.get("stdin", ""), data.get("lesson")
    if not isinstance(code, str) or not code.strip():
        return jsonify(error="Escribe algo de código primero."), 400
    if not isinstance(stdin, str):
        stdin = ""
    if not rate_limit(f"rl:run:{g.user['id']}", RUN_LIMIT, RUN_WINDOW):
        return jsonify(error=f"Límite de {RUN_LIMIT} ejecuciones por minuto. Espera un momento."), 429

    lesson_id = None
    if isinstance(lesson_slug, str):
        with db.pg_cursor() as cur:
            cur.execute("SELECT id FROM lessons WHERE slug=%s", (lesson_slug,))
            row = cur.fetchone()
            lesson_id = row["id"] if row else None

    result = run_code(code, stdin)
    report = audit_code(code, result)
    report_id = safe_persist(g.user["id"], "lesson" if lesson_id else "free", code, stdin,
                             result, report, lesson_id=lesson_id)
    update_state(g.user["id"], "ok" if result["ok"] else "error", report["score"])
    return jsonify(result=result, audit=report, report_id=report_id)


@app.post("/api/challenges/<slug>/submit")
@login_required
def submit_challenge(slug):
    data = request.get_json(silent=True) or {}
    code = data.get("code", "")
    if not isinstance(code, str) or not code.strip():
        return jsonify(error="Escribe tu solución primero."), 400
    if not rate_limit(f"rl:run:{g.user['id']}", RUN_LIMIT, RUN_WINDOW):
        return jsonify(error=f"Límite de {RUN_LIMIT} ejecuciones por minuto. Espera un momento."), 429

    with db.pg_cursor() as cur:
        cur.execute("SELECT * FROM challenges WHERE slug=%s", (slug,))
        challenge = cur.fetchone()
    if not challenge:
        return jsonify(error="Reto no encontrado."), 404

    result = run_code(code, challenge["test_input"])
    passed = result["ok"] and normalize(result["stdout"]) == normalize(challenge["expected_output"])
    report = audit_code(code, result)
    attempts = db.get_redis().incr(f"attempts:{g.user['id']}:{challenge['id']}")  # contador de intentos
    report_id = safe_persist(g.user["id"], "challenge", code, challenge["test_input"], result,
                             report, challenge_id=challenge["id"])

    points_awarded = 0
    if passed:
        with db.pg_cursor() as cur:
            cur.execute(
                "INSERT INTO challenge_completions (user_id, challenge_id, attempts) VALUES (%s,%s,%s) "
                "ON CONFLICT (user_id, challenge_id) DO NOTHING RETURNING id",
                (g.user["id"], challenge["id"], attempts))
            if cur.fetchone():  # primera vez: se otorgan puntos
                points_awarded = challenge["points"]
                cur.execute("UPDATE users SET points = points + %s WHERE id=%s",
                            (points_awarded, g.user["id"]))
    update_state(g.user["id"], "passed" if passed else "failed", report["score"])
    return jsonify(result=result, audit=report, report_id=report_id, passed=passed,
                   attempts=attempts, points_awarded=points_awarded)


# ---------------------------------------------------------------------- Historial
@app.get("/api/history")
@login_required
def history():
    uid = g.user["id"]
    with db.pg_cursor() as cur:
        cur.execute(
            "SELECT g.score, g.grade, g.created_at, COALESCE(l.title, c.title, 'Práctica libre') AS origin "
            "FROM auditor_grades g LEFT JOIN lessons l ON l.id=g.lesson_id "
            "LEFT JOIN challenges c ON c.id=g.challenge_id "
            "WHERE g.user_id=%s ORDER BY g.created_at DESC LIMIT 15", (uid,))
        grades = cur.fetchall()
        cur.execute(
            "SELECT l.title, COUNT(*) AS runs, BOOL_OR(h.success) AS succeeded "
            "FROM lesson_history h JOIN lessons l ON l.id=h.lesson_id "
            "WHERE h.user_id=%s GROUP BY l.title ORDER BY MAX(h.created_at) DESC", (uid,))
        lesson_progress = cur.fetchall()
    traces = [clean_doc(d) for d in
              db.get_mongo().code_traces.find({"user_id": uid}, {"code": 0, "stdin": 0})
              .sort("created_at", -1).limit(10)]
    return jsonify(grades=grades, lesson_progress=lesson_progress, traces=traces)


@app.get("/api/reports/<report_id>")
@login_required
def report(report_id):
    if not ObjectId.is_valid(report_id):
        return jsonify(error="Identificador inválido."), 400
    doc = db.get_mongo().audit_reports.find_one({"_id": ObjectId(report_id), "user_id": g.user["id"]})
    if not doc:
        return jsonify(error="Informe no encontrado."), 404
    return jsonify(report=clean_doc(doc))


@app.errorhandler(413)
def too_large(_):
    return jsonify(error="La petición es demasiado grande."), 413


if __name__ != "__main__":
    logging.basicConfig(level=logging.INFO)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
