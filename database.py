"""Conexiones a PostgreSQL, Redis y MongoDB.

Cada conexión se crea una sola vez por proceso (patrón singleton) y se
configura con variables de entorno, que en Railway inyectan los plugins.
"""
import os
from contextlib import contextmanager

import redis
from psycopg2.extras import RealDictCursor
from psycopg2.pool import ThreadedConnectionPool
from pymongo import ASCENDING, DESCENDING, MongoClient

_pg_pool = None
_redis = None
_mongo_db = None


# ---------------------------------------------------------------- PostgreSQL
@contextmanager
def pg_cursor():
    """Entrega un cursor (filas como dict) y hace commit o rollback al salir."""
    conn = _pg_pool.getconn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            yield cur
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        _pg_pool.putconn(conn)


def init_postgres():
    """Crea el pool y aplica schema.sql. El lock evita carreras entre workers."""
    global _pg_pool
    if _pg_pool is None:
        _pg_pool = ThreadedConnectionPool(
            1, int(os.getenv("PG_MAX_CONN", "10")), os.environ["DATABASE_URL"]
        )
        schema_path = os.path.join(os.path.dirname(__file__), "schema.sql")
        with open(schema_path, encoding="utf-8") as fh:
            schema = fh.read()
        with pg_cursor() as cur:
            cur.execute("SELECT pg_advisory_xact_lock(727001)")
            cur.execute(schema)
    return _pg_pool


# --------------------------------------------------------------------- Redis
def get_redis():
    """Cliente Redis (sesiones, caché, contadores y estado en tiempo real)."""
    global _redis
    if _redis is None:
        _redis = redis.Redis.from_url(
            os.getenv("REDIS_URL", "redis://localhost:6379/0"),
            decode_responses=True,
            socket_timeout=3,
            socket_connect_timeout=3,
        )
    return _redis


# ------------------------------------------------------------------- MongoDB
def get_mongo():
    """Base MongoDB con informes de auditoría y trazas de código."""
    global _mongo_db
    if _mongo_db is None:
        uri = (
            os.getenv("MONGO_URL")
            or os.getenv("MONGODB_URI")
            or "mongodb://localhost:27017"
        )
        client = MongoClient(uri, serverSelectionTimeoutMS=5000)
        client.admin.command("ping")
        _mongo_db = client[os.getenv("MONGO_DB", "mxl")]
        for name in ("audit_reports", "code_traces"):
            _mongo_db[name].create_index(
                [("user_id", ASCENDING), ("created_at", DESCENDING)]
            )
    return _mongo_db


# ------------------------------------------------------------------- Utilidades
def init_all():
    """Conecta las tres bases; si alguna falla, el arranque falla rápido."""
    init_postgres()
    get_redis().ping()
    get_mongo()


def health():
    """Estado de cada base de datos para el endpoint /health."""
    status = {}
    try:
        with pg_cursor() as cur:
            cur.execute("SELECT 1")
        status["postgres"] = "ok"
    except Exception as exc:
        status["postgres"] = f"error: {type(exc).__name__}"
    try:
        get_redis().ping()
        status["redis"] = "ok"
    except Exception as exc:
        status["redis"] = f"error: {type(exc).__name__}"
    try:
        get_mongo().command("ping")
        status["mongo"] = "ok"
    except Exception as exc:
        status["mongo"] = f"error: {type(exc).__name__}"
    return status
