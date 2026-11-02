"""Terminal Linux simulada: practicar administración sin riesgo real.

Todo ocurre sobre un sistema de archivos virtual guardado por usuario en Redis.
Nada de esto toca el servidor: permisos, sudo, nano/vim y systemctl son simulados.
grep y sed buscan texto literal (no expresiones regulares), por seguridad.
"""
import difflib
import hashlib
import posixpath
import shlex

import http_safe

CONF = "/etc/webapp/app.conf"
BACKUP = CONF + ".bak"
ORIGINAL_CONF = "# Configuración de webapp\nport=8080\nhost=0.0.0.0\nworkers=2\nlog_level=info\n"
SCENARIO = "restart-seguro"
OWNERS = {"user": "alumno", "root": "root"}
HOME = "/home/alumno"
MAX_FILE, MAX_FILES = 20_000, 60
ALLOWED_KEYS = {"port", "host", "workers", "log_level"}

HELP = ("Comandos: ls cd pwd cat head tail wc grep cp mv rm mkdir touch echo sed diff\n"
        "          nano vim sudo systemctl webapp curl tee whoami id help clear\n"
        "Operadores: |  &&  >  >>")

GUIDE = [
    {"title": "Protocolo de cero riesgos", "lines": [
        "1. Respalda: sudo cp /etc/webapp/app.conf /etc/webapp/app.conf.bak",
        "2. Busca con precisión: grep -n port /etc/webapp/app.conf",
        "3. Edita lo mínimo: sudo nano /etc/webapp/app.conf",
        "4. Compara: diff /etc/webapp/app.conf.bak /etc/webapp/app.conf",
        "5. Valida ANTES de reiniciar: webapp check",
        "6. Reinicia y verifica: sudo systemctl restart webapp && systemctl status webapp",
        "7. Si algo falla, vuelve atrás: sudo cp /etc/webapp/app.conf.bak /etc/webapp/app.conf"]},
    {"title": "nano", "lines": [
        "Ctrl+O guarda, Ctrl+X sale, Ctrl+W busca texto (en el nano real).",
        "Ctrl+K corta la línea, Ctrl+U la pega, Ctrl+_ salta a una línea."]},
    {"title": "vim", "lines": [
        "i entra en modo insertar; Esc vuelve al modo normal.",
        ":w guarda, :q sale, :wq guarda y sale, :q! sale sin guardar.",
        "/texto busca, n va al siguiente resultado, dd borra una línea, u deshace."]},
    {"title": "sudo", "lines": [
        "sudo ejecuta UN comando como administrador; úsalo solo cuando haga falta.",
        "/etc y /var pertenecen a root: sin sudo no puedes escribir ahí.",
        "'sudo echo x > archivo' falla porque la redirección la abre tu usuario.",
        "Forma correcta: echo x | sudo tee archivo   (o  >> con tee -a)."]},
    {"title": "Búsqueda precisa", "lines": [
        "grep -n texto archivo muestra la línea exacta; -i ignora mayúsculas.",
        "cat /var/log/webapp.log | grep ERROR | tail -n 5",
        "sed 's/viejo/nuevo/' archivo muestra el cambio sin guardarlo; con -i lo guarda."]},
]


class ShellError(Exception):
    """Error de un comando; el mensaje se muestra tal cual."""


def initial_state():
    log = ("2026-11-02 08:01:12 INFO  webapp iniciado en puerto 8080\n"
           "2026-11-02 08:03:40 WARN  conexión lenta a la base de datos\n"
           "2026-11-02 08:07:55 ERROR timeout al consultar /api/pedidos\n"
           "2026-11-02 08:09:02 INFO  health check ok\n")
    return {
        "files": {
            f"{HOME}/notas.txt": {"c": "Tarea: cambiar el puerto de webapp a 8081 sin tumbar el servicio.\n", "o": "user"},
            CONF: {"c": ORIGINAL_CONF, "o": "root"},
            "/var/log/webapp.log": {"c": log, "o": "root"},
        },
        "dirs": ["/", "/home", HOME, "/etc", "/etc/webapp", "/var", "/var/log"],
        "cwd": HOME, "service": "active", "events": [], "checked_hash": None,
        "restarted_ok": False, "pending": None, "completed": False,
    }


def validate_conf(text):
    """Devuelve None si la configuración es válida, o el motivo del error."""
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if not sep or key not in ALLOWED_KEYS:
            return f"app.conf:{number}: línea inválida '{line}'"
        if key == "port" and not (value.isdigit() and 1 <= int(value) <= 65535):
            return f"app.conf:{number}: port debe ser un número entre 1 y 65535"
        if key == "workers" and not (value.isdigit() and 1 <= int(value) <= 16):
            return f"app.conf:{number}: workers debe estar entre 1 y 16"
        if key == "log_level" and value not in ("debug", "info", "warning", "error"):
            return f"app.conf:{number}: log_level inválido '{value}'"
    return None


def _hash(text):
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def goals(state):
    files = state["files"]
    conf = files[CONF]["c"]
    port_ok = "port=8081" in [ln.strip() for ln in conf.splitlines()]
    return [
        {"id": "backup", "text": "Respaldar app.conf ANTES de tocarlo (app.conf.bak con el contenido original)",
         "done": files.get(BACKUP, {}).get("c") == ORIGINAL_CONF},
        {"id": "edit", "text": "Cambiar el puerto a 8081 en app.conf", "done": port_ok},
        {"id": "check", "text": "Validar la configuración con 'webapp check'",
         "done": state.get("checked_hash") == _hash(conf) and validate_conf(conf) is None},
        {"id": "restart", "text": "Reiniciar con sudo y confirmar que el servicio sigue activo",
         "done": state["service"] == "active" and state.get("restarted_ok") and port_ok},
    ]


def score(state):
    return max(0, 100 - 25 * len(state["events"]))


def prompt(state):
    cwd = state["cwd"]
    if cwd == HOME or cwd.startswith(HOME + "/"):
        cwd = "~" + cwd[len(HOME):]
    return f"alumno@mxl:{cwd}$"


class Shell:
    def __init__(self, state):
        self.s = state
        self.edit = None
        self.notes = []
        self.exit = 0

    # ------------------------------------------------------- rutas y permisos
    def abspath(self, path):
        if path == "~" or path.startswith("~/"):
            path = HOME + path[1:]
        if not path.startswith("/"):
            path = posixpath.join(self.s["cwd"], path)
        return posixpath.normpath(path)

    def owner(self, path):
        if path in self.s["files"]:
            return self.s["files"][path]["o"]
        return "root" if path.startswith(("/etc", "/var")) else "user"

    def check_write(self, path, sudo):
        if path in self.s["dirs"]:
            raise ShellError(f"{path}: Is a directory")
        if self.owner(path) == "root" and not sudo:
            raise ShellError(f"{path}: Permission denied")
        if posixpath.dirname(path) not in self.s["dirs"]:
            raise ShellError(f"{path}: No such file or directory")

    def write_file(self, path, content, sudo):
        """Única vía de escritura: aplica permisos, límites y alertas de riesgo."""
        self.check_write(path, sudo)
        if len(content) > MAX_FILE:
            raise ShellError("archivo demasiado grande")
        if path not in self.s["files"] and len(self.s["files"]) >= MAX_FILES:
            raise ShellError("límite de archivos alcanzado")
        if path == CONF:
            if BACKUP not in self.s["files"]:
                self.s["events"].append("unbacked_edit")
                self.notes.append("⚠ Modificaste app.conf SIN respaldo previo. En producción, copia primero: "
                                  "sudo cp /etc/webapp/app.conf /etc/webapp/app.conf.bak")
            self.s["restarted_ok"] = False
        self.s["files"][path] = {"c": content, "o": self.owner(path)}

    def save_edit(self, content):
        pending = self.s.get("pending")
        if not pending:
            raise ShellError("no hay ninguna edición abierta")
        self.write_file(pending["path"], content, pending["sudo"])
        self.s["pending"] = None
        return f"Guardado: {pending['path']}"

    # ------------------------------------------------------------- intérprete
    def execute(self, line):
        line = line.strip()
        if not line:
            return ""
        try:
            lexer = shlex.shlex(line, posix=True, punctuation_chars="|&<>;")
            lexer.whitespace_split = True
            tokens = list(lexer)
        except ValueError as exc:
            return f"bash: error de sintaxis ({exc})"
        for tok in tokens:
            if set(tok) <= set("|&<>;") and tok not in ("|", "&&", ">", ">>"):
                return f"bash: operador no soportado: {tok}"
        out = []
        for segment in self._split(tokens, "&&"):
            text, ok = self._pipeline(segment)
            if text:
                out.append(text)
            if not ok:
                break
        out.extend(self.notes)
        return "\n".join(out)

    @staticmethod
    def _split(tokens, sep):
        parts, current = [], []
        for tok in tokens:
            if tok == sep:
                parts.append(current)
                current = []
            else:
                current.append(tok)
        parts.append(current)
        return parts

    def _pipeline(self, segment):
        data, ok = None, True
        for part in self._split(segment, "|"):
            if not part:
                return "bash: error de sintaxis cerca de '|' o '&&'", False
            data, ok = self._command(part, data)
            if not ok:
                break
        return data or "", ok

    def _command(self, tokens, stdin):
        sudo = False
        if tokens[0] == "sudo":
            sudo, tokens = True, tokens[1:]
            if not tokens:
                return "usage: sudo COMANDO", False
        redirect = None
        for i, tok in enumerate(tokens):
            if tok in (">", ">>"):
                if i + 1 >= len(tokens):
                    return "bash: error de sintaxis cerca de '>'", False
                redirect, tokens = (tok, tokens[i + 1]), tokens[:i] + tokens[i + 2:]
                break
        if not tokens:
            return "", True
        name, args = tokens[0], tokens[1:]
        handler = getattr(self, "c_" + name, None) if name.isalnum() else None
        if handler is None:
            return f"{name}: command not found", False
        self.exit = 0
        try:
            out = handler(args, stdin, sudo) or ""
        except ShellError as exc:
            return f"{name}: {exc}", False
        except ValueError:
            return f"{name}: argumento inválido", False
        if redirect:
            path = self.abspath(redirect[1])
            text = out + ("\n" if out and not out.endswith("\n") else "")
            if redirect[0] == ">>" and path in self.s["files"]:
                text = self.s["files"][path]["c"] + text
            try:
                self.write_file(path, text, sudo=False)  # la redirección la abre TU usuario
            except ShellError as exc:
                return f"bash: {exc}", False
            out = ""
        return out, self.exit == 0

    def _text(self, args, stdin):
        paths = [a for a in args if not a.startswith("-")]
        if not paths:
            if stdin is None:
                raise ShellError("falta un archivo (o usa una tubería |)")
            return stdin
        chunks = []
        for arg in paths:
            path = self.abspath(arg)
            if path in self.s["dirs"]:
                raise ShellError(f"{arg}: Is a directory")
            if path not in self.s["files"]:
                raise ShellError(f"{arg}: No such file or directory")
            chunks.append(self.s["files"][path]["c"])
        return "".join(chunks)

    # --------------------------------------------------------------- comandos
    def c_help(self, args, stdin, sudo):
        return HELP

    def c_pwd(self, args, stdin, sudo):
        return self.s["cwd"]

    def c_whoami(self, args, stdin, sudo):
        return "root" if sudo else "alumno"

    def c_id(self, args, stdin, sudo):
        return "uid=0(root) gid=0(root)" if sudo else "uid=1000(alumno) gid=1000(alumno)"

    def c_cd(self, args, stdin, sudo):
        path = self.abspath(args[0] if args else "~")
        if path not in self.s["dirs"]:
            raise ShellError(f"{path}: No such file or directory")
        self.s["cwd"] = path
        return ""

    def c_ls(self, args, stdin, sudo):
        flags = "".join(a[1:] for a in args if a.startswith("-"))
        lines = []
        for target in [a for a in args if not a.startswith("-")] or ["."]:
            path = self.abspath(target)
            if path in self.s["files"]:
                names = [path]
            elif path in self.s["dirs"]:
                names = sorted(p for p in list(self.s["files"]) + self.s["dirs"]
                               if p != "/" and posixpath.dirname(p) == path)
            else:
                raise ShellError(f"cannot access '{target}': No such file or directory")
            for full in names:
                base = posixpath.basename(full)
                if base.startswith(".") and "a" not in flags:
                    continue
                is_dir = full in self.s["dirs"]
                label = base + ("/" if is_dir else "")
                if "l" in flags:
                    owner = OWNERS[self.owner(full)]
                    lines.append(f"{'d' if is_dir else '-'}rw-r--r-- {owner:<6} {label}")
                else:
                    lines.append(label)
        return "\n".join(lines) if "l" in flags else "  ".join(lines)

    def c_cat(self, args, stdin, sudo):
        return self._text(args, stdin).rstrip("\n")

    def _count_args(self, args):
        count, rest, it = 10, [], iter(args)
        for arg in it:
            if arg == "-n":
                count = int(next(it, "10"))
            elif arg.startswith("-") and arg[1:].isdigit():
                count = int(arg[1:])
            else:
                rest.append(arg)
        return count, rest

    def c_head(self, args, stdin, sudo):
        count, rest = self._count_args(args)
        return "\n".join(self._text(rest, stdin).splitlines()[:count])

    def c_tail(self, args, stdin, sudo):
        count, rest = self._count_args(args)
        return "\n".join(self._text(rest, stdin).splitlines()[-count:] if count else [])

    def c_wc(self, args, stdin, sudo):
        text = self._text([a for a in args if a != "-l"], stdin)
        return str(len(text.splitlines()))

    def c_grep(self, args, stdin, sudo):
        flags = "".join(a[1:] for a in args if a.startswith("-"))
        rest = [a for a in args if not a.startswith("-")]
        if not rest:
            raise ShellError("uso: grep [-n] [-i] TEXTO [ARCHIVO]")
        needle, files = rest[0], rest[1:]
        needle = needle.lower() if "i" in flags else needle
        sources = []
        if files:
            for f in files:
                sources.append((f, self._text([f], None)))
        else:
            sources.append((None, self._text([], stdin)))
        hits = []
        for fname, text in sources:
            for n, line in enumerate(text.splitlines(), 1):
                if needle in (line.lower() if "i" in flags else line):
                    prefix = (f"{fname}:" if len(files) > 1 else "") + (f"{n}:" if "n" in flags else "")
                    hits.append(prefix + line)
        if not hits:
            self.exit = 1
        return "\n".join(hits)

    def _paths2(self, args, usage):
        paths = [a for a in args if not a.startswith("-")]
        if len(paths) != 2:
            raise ShellError(usage)
        src, dst = self.abspath(paths[0]), self.abspath(paths[1])
        if src not in self.s["files"]:
            raise ShellError(f"cannot stat '{paths[0]}': No such file or directory")
        if dst in self.s["dirs"]:
            dst = posixpath.join(dst, posixpath.basename(src))
        return src, dst

    def c_cp(self, args, stdin, sudo):
        src, dst = self._paths2(args, "uso: cp ORIGEN DESTINO")
        self.write_file(dst, self.s["files"][src]["c"], sudo)
        return ""

    def c_mv(self, args, stdin, sudo):
        src, dst = self._paths2(args, "uso: mv ORIGEN DESTINO")
        self.check_write(src, sudo)
        self.write_file(dst, self.s["files"][src]["c"], sudo)
        del self.s["files"][src]
        return ""

    def c_rm(self, args, stdin, sudo):
        paths = [a for a in args if not a.startswith("-")]
        if not paths:
            raise ShellError("uso: rm ARCHIVO")
        for arg in paths:
            path = self.abspath(arg)
            if path in self.s["dirs"]:
                raise ShellError(f"{arg}: es un directorio (rm -r no está habilitado en el simulador)")
            if path not in self.s["files"]:
                raise ShellError(f"cannot remove '{arg}': No such file or directory")
            self.check_write(path, sudo)
            del self.s["files"][path]
        return ""

    def c_mkdir(self, args, stdin, sudo):
        for arg in [a for a in args if not a.startswith("-")]:
            path = self.abspath(arg)
            if path in self.s["dirs"] or path in self.s["files"]:
                raise ShellError(f"cannot create directory '{arg}': File exists")
            self.check_write(path, sudo)
            self.s["dirs"].append(path)
        return ""

    def c_touch(self, args, stdin, sudo):
        for arg in args:
            path = self.abspath(arg)
            if path not in self.s["files"]:
                self.write_file(path, "", sudo)
        return ""

    def c_echo(self, args, stdin, sudo):
        return " ".join(args)

    def c_tee(self, args, stdin, sudo):
        if stdin is None:
            raise ShellError("tee necesita una tubería: echo texto | sudo tee archivo")
        paths = [a for a in args if not a.startswith("-")]
        if len(paths) != 1:
            raise ShellError("uso: tee [-a] ARCHIVO")
        path = self.abspath(paths[0])
        text = stdin + ("\n" if stdin and not stdin.endswith("\n") else "")
        if "-a" in args and path in self.s["files"]:
            text = self.s["files"][path]["c"] + text
        self.write_file(path, text, sudo)
        return stdin

    def c_sed(self, args, stdin, sudo):
        inplace = "-i" in args
        rest = [a for a in args if a != "-i"]
        if not rest:
            raise ShellError("uso: sed [-i] 's/viejo/nuevo/[g]' ARCHIVO")
        expr = rest[0]
        pieces = expr.split("/")
        if len(pieces) != 4 or pieces[0] != "s" or pieces[3] not in ("", "g") or not pieces[1]:
            raise ShellError("solo se admite s/texto/reemplazo/[g] (texto literal)")
        _, old, new, mode = pieces
        text = self._text(rest[1:], stdin)
        count = -1 if mode == "g" else 1
        result = "\n".join(line.replace(old, new, count) for line in text.split("\n"))
        if inplace:
            files = [a for a in rest[1:] if not a.startswith("-")]
            if len(files) != 1:
                raise ShellError("sed -i necesita un archivo")
            self.write_file(self.abspath(files[0]), result, sudo)
            return ""
        return result.rstrip("\n")

    def c_diff(self, args, stdin, sudo):
        paths = [a for a in args if not a.startswith("-")]
        if len(paths) != 2:
            raise ShellError("uso: diff ARCHIVO1 ARCHIVO2")
        a, b = self._text([paths[0]], None), self._text([paths[1]], None)
        diff = list(difflib.unified_diff(a.splitlines(), b.splitlines(), paths[0], paths[1], lineterm=""))
        if diff:
            self.exit = 1
        return "\n".join(diff)

    def _open_editor(self, editor, args, sudo):
        paths = [a for a in args if not a.startswith("-")]
        if len(paths) != 1:
            raise ShellError(f"uso: {editor} ARCHIVO")
        path = self.abspath(paths[0])
        if path in self.s["dirs"]:
            raise ShellError(f"{paths[0]}: Is a directory")
        files = self.s["files"]
        self.edit = {"path": path, "content": files[path]["c"] if path in files else "",
                     "editor": editor, "sudo": sudo}
        self.s["pending"] = {"path": path, "sudo": sudo}
        if self.owner(path) == "root" and not sudo:
            return "[Archivo de solo lectura: podrás verlo, pero para guardar abre con 'sudo " + editor + "']"
        return ""

    def c_nano(self, args, stdin, sudo):
        return self._open_editor("nano", args, sudo)

    def c_vim(self, args, stdin, sudo):
        return self._open_editor("vim", args, sudo)

    c_vi = c_vim

    def c_webapp(self, args, stdin, sudo):
        if args != ["check"]:
            raise ShellError("uso: webapp check")
        conf = self.s["files"][CONF]["c"]
        error = validate_conf(conf)
        if error:
            self.exit = 1
            return f"{error}\nconfiguración INVÁLIDA: no reinicies hasta corregirla."
        self.s["checked_hash"] = _hash(conf)
        return "app.conf: sintaxis OK"

    def c_systemctl(self, args, stdin, sudo):
        if len(args) != 2 or args[1].split(".")[0] != "webapp":
            raise ShellError("uso: systemctl status|restart webapp")
        conf = self.s["files"][CONF]["c"]
        if args[0] == "status":
            port = next((ln.split("=")[1].strip() for ln in conf.splitlines() if ln.strip().startswith("port=")), "?")
            if self.s["service"] == "active":
                return f"● webapp.service - Aplicación MXL\n   Active: active (running)\n   Puerto en uso: {port}"
            self.exit = 3
            return "● webapp.service - Aplicación MXL\n   Active: failed (Result: exit-code)"
        if args[0] == "restart":
            if not sudo:
                raise ShellError("Failed to restart webapp.service: Interactive authentication required (usa sudo).")
            error = validate_conf(conf)
            if error:
                self.s["service"] = "failed"
                self.s["events"].append("crash")
                self.exit = 1
                return (f"Job for webapp.service failed.\n{error}\n"
                        "💥 El servicio cayó. Restaura el respaldo (sudo cp app.conf.bak app.conf) y reinicia.")
            self.s["service"], self.s["restarted_ok"] = "active", True
            return "webapp.service reiniciado correctamente."
        raise ShellError("acción no soportada (usa status o restart)")

    def c_curl(self, args, stdin, sudo):
        method, headers, body, show, url = "GET", {}, None, False, None
        it = iter(args)
        for arg in it:
            if arg == "-X":
                method = next(it, "GET")
            elif arg == "-H":
                key, _, value = next(it, "").partition(":")
                headers[key.strip()] = value.strip()
            elif arg in ("-d", "--data"):
                body, method = next(it, ""), ("POST" if method == "GET" else method)
            elif arg == "-i":
                show = True
            elif not arg.startswith("-"):
                url = arg
        if not url:
            raise ShellError("uso: curl [-i] [-X MÉTODO] [-H 'K: V'] [-d DATOS] URL")
        try:
            res = http_safe.fetch(method, url, headers, body)
        except http_safe.HttpBlocked as exc:
            self.exit = 7
            raise ShellError(f"(7) {exc}")
        head = ""
        if show:
            head = f"HTTP {res['status']} {res['reason']}\n" + "\n".join(f"{k}: {v}" for k, v in res["headers"].items()) + "\n\n"
        return head + res["body"][:3000]
