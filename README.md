# MXL Lab

Práctica de programación en Python con auditoría automática de código.
Flask + PostgreSQL + Redis + MongoDB.

## Estructura
- `app.py`: servidor Flask, rutas y lógica principal
- `database.py`: conexiones a PostgreSQL, Redis y MongoDB
- `sandbox.py`: ejecución aislada de código del usuario
- `auditor.py`: análisis estático y calificación (0-100, A-F)
- `schema.sql`: tablas y contenido inicial (se aplica solo al arrancar)
- `templates/`, `static/`: interfaz tipo terminal

## Desarrollo local
```bash
docker compose up -d
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python app.py            # http://localhost:5000
```

## Despliegue en Railway
1. Sube el repositorio a GitHub.
2. En Railway: **New Project → Deploy from GitHub repo**.
3. Añade los plugins **PostgreSQL**, **Redis** y **MongoDB** al mismo proyecto.
4. En las variables del servicio web referencia las de cada plugin:
   `DATABASE_URL`, `REDIS_URL` y `MONGO_URL` (por ejemplo `${{Postgres.DATABASE_URL}}`).
5. Despliega. Railway usa `railway.json` y comprueba `/health`.

## Seguridad del sandbox
Lista blanca de módulos, builtins restringidos, bloqueo de atributos `__dunder__`,
subproceso con entorno vacío, límites de CPU/memoria/archivos/procesos y timeout de 5 s.
No aísla la red: para exponer el servicio a usuarios desconocidos, ejecuta el código
en un contenedor aparte (nsjail, gVisor o Firecracker).
