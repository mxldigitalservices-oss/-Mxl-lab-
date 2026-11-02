"""Cliente HTTP seguro para practicar APIs sin abrir el servidor a ataques SSRF.

Solo permite destinos públicos: resuelve el dominio, rechaza IPs privadas,
loopback, link-local y reservadas, y conecta a la IP ya validada (evita DNS
rebinding). Revalida cada redirección y limita tiempo y tamaño.
"""
import http.client
import ipaddress
import socket
import ssl
import time
import urllib.parse

ALLOWED_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"}
ALLOWED_PORTS = {80, 443, 3000, 5000, 8000, 8080, 8443}
MAX_BODY = 100_000
TIMEOUT = 8
MAX_REDIRECTS = 3
DROP_HEADERS = {"host", "content-length", "connection", "transfer-encoding"}


class HttpBlocked(Exception):
    """Petición rechazada o fallida; el mensaje es apto para mostrar al usuario."""


def _resolve(host, port):
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror:
        raise HttpBlocked("no se pudo resolver el dominio")
    for _, _, _, _, sockaddr in infos:
        ip = ipaddress.ip_address(sockaddr[0])
        if ip.version == 6 and ip.ipv4_mapped:
            ip = ip.ipv4_mapped
        if not ip.is_global:
            raise HttpBlocked(f"destino no público bloqueado ({ip})")
    return infos[0][4][0]


class _PinnedHTTP(http.client.HTTPConnection):
    def __init__(self, host, port, ip, **kw):
        super().__init__(host, port, **kw)
        self._ip = ip

    def connect(self):
        self.sock = socket.create_connection((self._ip, self.port), self.timeout)


class _PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, host, port, ip, **kw):
        super().__init__(host, port, context=ssl.create_default_context(), **kw)
        self._ip = ip

    def connect(self):
        raw = socket.create_connection((self._ip, self.port), self.timeout)
        self.sock = self._context.wrap_socket(raw, server_hostname=self.host)


def fetch(method, url, headers=None, body=None):
    """Hace la petición y devuelve estado, cabeceras y cuerpo (recortado)."""
    method = (method or "GET").upper()
    if method not in ALLOWED_METHODS:
        raise HttpBlocked(f"método no permitido: {method}")
    if isinstance(body, str):
        body = body.encode("utf-8")
    start = time.monotonic()
    for _ in range(MAX_REDIRECTS + 1):
        parts = urllib.parse.urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise HttpBlocked("solo se permiten URLs http(s) completas")
        try:
            port = parts.port or (443 if parts.scheme == "https" else 80)
        except ValueError:
            raise HttpBlocked("puerto inválido")
        if port not in ALLOWED_PORTS:
            raise HttpBlocked(f"puerto {port} no permitido (usa {sorted(ALLOWED_PORTS)})")
        ip = _resolve(parts.hostname, port)
        cls = _PinnedHTTPS if parts.scheme == "https" else _PinnedHTTP
        conn = cls(parts.hostname, port, ip, timeout=TIMEOUT)
        send = {k: str(v) for k, v in (headers or {}).items() if k.lower() not in DROP_HEADERS}
        send.setdefault("User-Agent", "MXL-Lab/1.0")
        path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
        try:
            conn.request(method, path, body=body, headers=send)
            resp = conn.getresponse()
            location = resp.getheader("Location")
            if resp.status in (301, 302, 303, 307, 308) and location:
                url = urllib.parse.urljoin(url, location)
                if resp.status in (301, 302, 303) and method != "HEAD":
                    method, body = "GET", None
                conn.close()
                continue
            data = resp.read(MAX_BODY + 1)
            result = {
                "status": resp.status, "reason": resp.reason, "url": url,
                "headers": dict(resp.getheaders()),
                "body": data[:MAX_BODY].decode("utf-8", errors="replace"),
                "truncated": len(data) > MAX_BODY,
                "ms": int((time.monotonic() - start) * 1000),
            }
            conn.close()
            return result
        except (OSError, http.client.HTTPException) as exc:
            raise HttpBlocked(f"error de conexión: {exc}")
    raise HttpBlocked("demasiadas redirecciones")
