"use strict";
// Extensiones v2: selector de lenguaje, vista previa HTML/CSS, terminal Linux simulada y probador HTTP.
// Reutiliza $, el, api, state y setStatus definidos en app.js.
(() => {
  const BROWSER = new Set(["html", "css"]);
  const DEMO = '<h1>Título</h1><p>Un párrafo con <a href="#">enlace</a> y <strong>negrita</strong>.</p>' +
    '<button>Botón</button><ul><li>Uno</li><li>Dos</li></ul>';
  const TEMPLATES = {
    python: 'print("Hola, MXL")\n',
    javascript: 'console.log("Hola, MXL");\n',
    c: '#include <stdio.h>\n\nint main(void) {\n    printf("Hola, MXL\\n");\n    return 0;\n}\n',
    cpp: '#include <iostream>\n\nint main() {\n    std::cout << "Hola, MXL" << std::endl;\n    return 0;\n}\n',
    bash: '#!/usr/bin/env bash\necho "Hola desde Bash"\nuname -a\n',
    ruby: 'puts "Hola, MXL"\n',
    php: '<?php\necho "Hola, MXL\\n";\n',
    html: '<!doctype html>\n<html>\n<body>\n  <h1>Hola, MXL</h1>\n  <script>document.body.append(" ¡JavaScript en el navegador!");</script>\n</body>\n</html>\n',
    css: 'body { font-family: sans-serif; background: #10141c; color: #e6edf3; padding: 16px; }\nh1 { color: #f0b34a; }\nbutton { padding: 8px 14px; }\n',
  };
  let lastTemplate = $("#code").value;

  // ------------------------------------------------------------ Lenguajes
  const isBrowser = () => BROWSER.has($("#lang").value);

  function refreshMode() {
    const browser = isBrowser();
    $("#preview").hidden = !browser;
    $("#out").hidden = browser;
    $("#stdin-box").hidden = browser;
  }

  function setLang(id) {            // lo usa app.js al elegir una lección o un reto
    if ([...$("#lang").options].some((o) => o.value === id)) $("#lang").value = id;
    lastTemplate = $("#code").value;
    refreshMode();
  }

  function preview() {
    const code = $("#code").value;
    $("#preview").srcdoc = $("#lang").value === "css" ? `<style>${code}</style>${DEMO}` : code;
    setStatus("Vista previa actualizada", false);
  }

  async function loadLanguages() {
    const data = await api("/api/languages");
    $("#lang").replaceChildren(...data.languages.map((l) => {
      const option = el("option", "", l.label);
      option.value = l.id;
      return option;
    }));
    $("#lang").value = "python";
  }

  $("#lang").addEventListener("change", () => {
    const code = $("#code").value;
    if (!code.trim() || code === lastTemplate) {   // no pisa lo que el usuario escribió
      $("#code").value = TEMPLATES[$("#lang").value] || "";
      updateGutter();
    }
    lastTemplate = $("#code").value;
    refreshMode();
  });

  // --------------------------------------------------------------- Pestañas
  document.querySelectorAll(".tab").forEach((tab) => tab.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t === tab));
    ["editor", "term", "http"].forEach((name) => { $(`#pane-${name}`).hidden = name !== tab.dataset.pane; });
    if (tab.dataset.pane === "term") termStart();
  }));

  // -------------------------------------------------------------- Terminal
  const T = { started: false, history: [], index: 0 };

  function tprint(text, className) {
    if (text === "" || text === undefined) return;
    const out = $("#term-out");
    out.append(el("div", className || "", text));
    out.scrollTop = out.scrollHeight;
  }

  function applyView(d) {
    $("#term-prompt").textContent = d.prompt;
    $("#goals").replaceChildren(...d.goals.map((g) => el("li", g.done ? "done" : "", `${g.done ? "✓" : "○"} ${g.text}`)));
  }

  function renderGuide(guide) {
    const body = $("#guide-body");
    body.replaceChildren();
    guide.forEach((section) => {
      body.append(el("strong", "", section.title), el("div", "", section.lines.join("\n") + "\n\n"));
    });
  }

  async function termStart() {
    if (T.started) return;
    if (!state.user) return tprint("Inicia sesión para usar la terminal.", "err");
    try {
      const d = await api("/api/term/state");
      T.started = true;
      applyView(d);
      renderGuide(d.guide);
      tprint("MXL Lab · terminal Linux simulada. Escribe 'help'. Tarea: cambiar el puerto de webapp a 8081 sin tumbar el servicio.", "muted");
    } catch (error) { tprint(error.message, "err"); }
  }

  function celebrate(done) {
    if (done) tprint(`Escenario completado · nota ${done.score}/100${done.points ? ` · +${done.points} puntos` : ""}`, "ok");
  }

  async function termSend(cmd) {
    tprint(`${$("#term-prompt").textContent} ${cmd}`, "muted");
    if (cmd.trim() === "clear") { $("#term-out").replaceChildren(); return; }
    try {
      const d = await api("/api/term", { cmd });
      tprint(d.output);
      applyView(d);
      celebrate(d.completed);
      if (d.completed) refreshMe();
      if (d.edit) openEditor(d.edit);
    } catch (error) { tprint(error.message, "err"); }
  }

  $("#term-in").addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      const cmd = e.target.value;
      e.target.value = "";
      if (cmd.trim()) { T.history.push(cmd); T.index = T.history.length; }
      termSend(cmd);
    } else if (e.key === "ArrowUp" && T.index > 0) {
      e.target.value = T.history[--T.index];
      e.preventDefault();
    } else if (e.key === "ArrowDown") {
      e.target.value = T.index < T.history.length - 1 ? T.history[++T.index] : "";
      T.index = Math.min(T.index + 1, T.history.length);
      e.preventDefault();
    }
  });

  $("#term-reset").addEventListener("click", async () => {
    if (!state.user) return;
    const d = await api("/api/term/reset", {});
    $("#term-out").replaceChildren();
    tprint(d.output, "muted");
    applyView(d);
  });

  // ------------------------------------------------- Editor tipo nano/vim
  let dirty = false;

  function openEditor(edit) {
    const nano = edit.editor === "nano";
    $("#nano-title").textContent = `${nano ? "GNU nano" : "vim"} (simulado) · ${edit.path}`;
    $("#nano-help").textContent = nano ? "Ctrl+O guarda · Ctrl+X sale"
      : "Simulador sin modos: Ctrl+S equivale a :w y Ctrl+X a :q";
    $("#nano-text").value = edit.content;
    $("#nano-msg").textContent = "";
    dirty = false;
    $("#nano").hidden = false;
    $("#nano-text").focus();
  }

  async function saveEditor() {
    try {
      const d = await api("/api/term/write", { content: $("#nano-text").value });
      dirty = false;
      $("#nano-msg").textContent = "";
      tprint(d.output);
      applyView(d);
      celebrate(d.completed);
      if (d.completed) refreshMe();
      $("#nano-msg").className = "ok";
      $("#nano-msg").textContent = "Guardado.";
    } catch (error) {
      $("#nano-msg").className = "err";
      $("#nano-msg").textContent = error.message + " (vuelve a abrir con sudo para poder guardar)";
    }
  }

  function closeEditor() {
    if (dirty && !confirm("¿Salir sin guardar los cambios?")) return;
    $("#nano").hidden = true;
    $("#term-in").focus();
  }

  $("#nano-text").addEventListener("input", () => { dirty = true; });
  $("#nano-text").addEventListener("keydown", (e) => {
    if (e.ctrlKey && (e.key === "o" || e.key === "s")) { e.preventDefault(); saveEditor(); }
    else if (e.ctrlKey && e.key === "x") { e.preventDefault(); closeEditor(); }
  });
  $("#nano-save").addEventListener("click", saveEditor);
  $("#nano-exit").addEventListener("click", closeEditor);

  // ------------------------------------------------------------- API HTTP
  $("#http-send").addEventListener("click", async () => {
    const out = $("#http-out");
    if (!state.user) { out.textContent = "Inicia sesión para enviar peticiones."; return; }
    let headers = {};
    const raw = $("#http-headers").value.trim();
    if (raw) {
      try { headers = JSON.parse(raw); } catch { out.textContent = "Las cabeceras deben ser JSON válido."; return; }
    }
    out.textContent = "Enviando…";
    try {
      const d = await api("/api/http", {
        method: $("#http-method").value, url: $("#http-url").value.trim(),
        headers, body: $("#http-body").value || null,
      });
      const head = Object.entries(d.headers).map(([k, v]) => `${k}: ${v}`).join("\n");
      out.textContent = `HTTP ${d.status} ${d.reason} · ${d.ms} ms\n${head}\n\n${d.body}${d.truncated ? "\n[…recortado]" : ""}`;
    } catch (error) { out.textContent = error.message; }
  });

  // --------------------------------------------------------------- Inicio
  window.MXL = { isBrowser, preview, setLang };
  loadLanguages().then(refreshMode);
})();
