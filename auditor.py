"""Centinela de auditoría MXL: análisis estático del código + resultado de ejecución.

Produce una puntuación 0-100, una nota (A-F), hallazgos y métricas.
"""
import ast
import re

PENALTY = {"error": 15, "warning": 7, "info": 2}
SNAKE = re.compile(r"^_{0,2}[a-z][a-z0-9_]*_{0,2}$")
CAPS = re.compile(r"^[A-Z][A-Za-z0-9]*$")
MAX_LINE = 100
MAX_COMPLEXITY = 10
MAX_DEPTH = 4


def _complexity(fn):
    """Complejidad ciclomática aproximada de una función."""
    total = 1
    for n in ast.walk(fn):
        if isinstance(n, (ast.If, ast.For, ast.While, ast.ExceptHandler, ast.IfExp,
                          ast.AsyncFor, ast.comprehension)):
            total += 1
        elif isinstance(n, ast.BoolOp):
            total += len(n.values) - 1
    return total


def _depth(node, level=0):
    """Profundidad máxima de anidamiento de estructuras de control."""
    deepest = level
    for child in ast.iter_child_nodes(node):
        nxt = level + 1 if isinstance(child, (ast.If, ast.For, ast.While, ast.With, ast.Try)) else level
        deepest = max(deepest, _depth(child, nxt))
    return deepest


def _grade(score):
    for limit, letter in ((90, "A"), (80, "B"), (70, "C"), (60, "D")):
        if score >= limit:
            return letter
    return "F"


def audit_code(code, run_result=None):
    findings = []

    def add(severity, rule, line, message):
        findings.append({"severity": severity, "rule": rule, "line": line, "message": message})

    lines = code.splitlines()
    metrics = {"lines": len(lines), "sloc": sum(1 for l in lines if l.strip() and not l.strip().startswith("#")),
               "functions": 0, "classes": 0, "max_complexity": 0, "max_depth": 0}

    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        add("error", "syntax", exc.lineno or 0, f"Error de sintaxis: {exc.msg}")
        tree = None

    if tree is not None:
        metrics["max_depth"] = _depth(tree)
        if metrics["max_depth"] > MAX_DEPTH:
            add("warning", "nesting", 0, f"Anidamiento de {metrics['max_depth']} niveles (máx. {MAX_DEPTH}).")

        imported, used = {}, set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                metrics["functions"] += 1
                cx = _complexity(node)
                metrics["max_complexity"] = max(metrics["max_complexity"], cx)
                if cx > MAX_COMPLEXITY:
                    add("warning", "complexity", node.lineno, f"'{node.name}' tiene complejidad {cx} (máx. {MAX_COMPLEXITY}).")
                if not SNAKE.match(node.name):
                    add("warning", "naming", node.lineno, f"'{node.name}' debería usar snake_case.")
                if len(node.body) > 5 and not ast.get_docstring(node):
                    add("info", "docstring", node.lineno, f"'{node.name}' no tiene docstring.")
                for default in node.args.defaults + node.args.kw_defaults:
                    if isinstance(default, (ast.List, ast.Dict, ast.Set)):
                        add("warning", "mutable-default", node.lineno, f"'{node.name}' usa un valor por defecto mutable.")
            elif isinstance(node, ast.ClassDef):
                metrics["classes"] += 1
                if not CAPS.match(node.name):
                    add("info", "naming", node.lineno, f"La clase '{node.name}' debería usar CapWords.")
            elif isinstance(node, ast.ExceptHandler) and node.type is None:
                add("warning", "bare-except", node.lineno, "Evita 'except:' sin tipo de excepción.")
            elif isinstance(node, ast.Import):
                for a in node.names:
                    imported[(a.asname or a.name).split(".")[0]] = node.lineno
            elif isinstance(node, ast.ImportFrom):
                for a in node.names:
                    imported[a.asname or a.name] = node.lineno
            elif isinstance(node, ast.Name):
                used.add(node.id)
        for name, line in imported.items():
            if name not in used:
                add("info", "unused-import", line, f"El import '{name}' no se usa.")

    for number, text in enumerate(lines, 1):
        if len(text) > MAX_LINE:
            add("info", "line-length", number, f"Línea de {len(text)} caracteres (máx. {MAX_LINE}).")
    if metrics["sloc"] > 15 and not any(l.strip().startswith("#") for l in lines):
        add("info", "comments", 0, "El programa no tiene comentarios.")

    if run_result:
        if run_result.get("blocked"):
            add("error", "sandbox", 0, "El código fue bloqueado por las reglas de seguridad.")
        elif run_result.get("timed_out"):
            add("error", "timeout", 0, "La ejecución excedió el tiempo límite.")
        elif not run_result.get("ok") and tree is not None:
            add("error", "runtime", 0, "El programa terminó con error en tiempo de ejecución.")

    score = max(0, 100 - sum(PENALTY[f["severity"]] for f in findings))
    return {"score": score, "grade": _grade(score), "findings": findings, "metrics": metrics}
