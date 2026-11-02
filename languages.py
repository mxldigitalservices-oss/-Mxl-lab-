"""Lenguajes soportados. Lo comparten la web (lista y validación) y el runner."""

LANGUAGES = [
    {"id": "python", "label": "Python", "mode": "server"},
    {"id": "javascript", "label": "JavaScript (Node)", "mode": "server"},
    {"id": "c", "label": "C", "mode": "server"},
    {"id": "cpp", "label": "C++", "mode": "server"},
    {"id": "bash", "label": "Bash (Linux)", "mode": "server"},
    {"id": "ruby", "label": "Ruby", "mode": "server"},
    {"id": "php", "label": "PHP", "mode": "server"},
    {"id": "html", "label": "HTML", "mode": "browser"},
    {"id": "css", "label": "CSS", "mode": "browser"},
]

# "server" se ejecuta en el runner; "browser" se previsualiza en un iframe aislado.
SERVER_LANGUAGES = {lang["id"] for lang in LANGUAGES if lang["mode"] == "server"}
