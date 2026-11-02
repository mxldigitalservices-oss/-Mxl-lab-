# MXL Lab

Práctica de programación multilenguaje y administración Linux con auditoría automática.
Flask + PostgreSQL + Redis + MongoDB + servicio runner aislado.

## Arquitectura
```
Navegador ──> web (Flask)  ──> PostgreSQL / Redis / MongoDB
                  │
                  └── HTTPS + token ──> runner (sin secretos): Python, Node, C, C++, Bash, Ruby, PHP
```
La web nunca ejecuta código de usuarios. HTML y CSS se previsualizan en un iframe aislado.

| Archivo | Función |
|---|---|
| `app.py` | Web, auth, ejecución, retos, historial |
| `lab_routes.py` | Lenguajes, terminal simulada, probador HTTP |
| `sandbox.py` | Cliente del runner |
| `runner/`, `Dockerfile.runner` | Servicio de ejecución multilenguaje |
| `terminal_sim.py` | Terminal Linux simulada (sudo, nano/vim, systemctl, curl) |
| `http_safe.py` | HTTP saliente seguro (anti-SSRF) |
| `auditor.py`, `schema.sql`, `database.py` | Auditoría, tablas, conexiones |

## Despliegue en Railway
**1. Web** (el servicio actual): variables `DATABASE_URL`, `REDIS_URL`, `MONGO_URL`,
`RUNNER_URL` (URL pública del runner) y `RUNNER_TOKEN`.

**2. Runner** (servicio nuevo, idealmente en OTRO proyecto de Railway):
- New → GitHub Repo → este repositorio.
- Settings → Config-as-code → *Railway Config File*: `/runner/railway.json`.
- Variables: solo `RUNNER_TOKEN` (el mismo que en la web). NO añadas variables de bases de datos.
- Opcional: `RUNNER_NETWORK=on` para que el código de usuarios tenga internet (solo en proyecto aparte).
- Settings → Networking → Generate Domain, y copia esa URL en `RUNNER_URL` de la web.

Genera el token con: `python3 -c "import secrets; print(secrets.token_urlsafe(48))"`

## Desarrollo local
```bash
docker compose up -d
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env     # ALLOW_LOCAL_EXEC=1 ejecuta código en tu máquina
python app.py
```
