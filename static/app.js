"use strict";
const $ = (selector) => document.querySelector(selector);
const state = { user: null, lessons: [], challenges: [], current: null };

/** Llamada JSON a la API; lanza Error con el mensaje del servidor. */
async function api(path, body) {
  const options = body === undefined ? {} : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  };
  const res = await fetch(path, options);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `Error ${res.status}`);
  return data;
}

function setStatus(message, isError) {
  const node = $("#status");
  node.textContent = message;
  node.className = isError ? "err" : "muted";
}

/** Crea un elemento con texto seguro (textContent, nunca innerHTML). */
function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

// ------------------------------------------------------------------ Editor
function updateGutter() {
  const count = $("#code").value.split("\n").length;
  $("#gutter").textContent = Array.from({ length: count }, (_, i) => i + 1).join("\n");
  $("#gutter").scrollTop = $("#code").scrollTop;
}

function onEditorKey(event) {
  if (event.key === "Tab") {           // inserta 4 espacios en vez de cambiar el foco
    event.preventDefault();
    const t = event.target;
    t.setRangeText("    ", t.selectionStart, t.selectionEnd, "end");
    updateGutter();
  } else if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
    event.preventDefault();
    execute("run");
  }
}

// ------------------------------------------------------------------ Sesión
function renderUser() {
  const logged = Boolean(state.user);
  $("#auth").hidden = logged;
  $("#session").hidden = !logged;
  if (logged) $("#who").textContent = `${state.user.username} · ${state.user.points} pts`;
}

async function refreshMe() {
  try { state.user = (await api("/api/me")).user; } catch { state.user = null; }
  renderUser();
}

async function authenticate(mode) {
  $("#auth-msg").textContent = "";
  try {
    await api(`/api/auth/${mode}`, { username: $("#user").value.trim(), password: $("#pass").value });
    $("#pass").value = "";
    await refreshMe();
    await Promise.all([loadLists(), loadHistory()]);
  } catch (error) { $("#auth-msg").textContent = error.message; }
}

async function logout() {
  await api("/api/auth/logout", {});
  state.user = null;
  renderUser();
  await loadLists();
  $("#history").textContent = "Inicia sesión para ver tu historial.";
}

// ------------------------------------------------------------ Lecciones y retos
function renderList(container, items, type) {
  container.replaceChildren(...items.map((item) => {
    const active = state.current && state.current.type === type && state.current.slug === item.slug;
    const button = el("button", "item" + (active ? " active" : ""), item.title + (item.completed ? "  ✓" : ""));
    button.addEventListener("click", () => select(type, item));
    return button;
  }));
}

function renderLists() {
  renderList($("#lessons"), state.lessons, "lesson");
  renderList($("#challenges"), state.challenges, "challenge");
}

async function loadLists() {
  const [l, c] = await Promise.all([api("/api/lessons"), api("/api/challenges")]);
  state.lessons = l.lessons;
  state.challenges = c.challenges;
  renderLists();
}

function select(type, item) {
  state.current = { type, slug: item.slug };
  $("#code").value = item.starter_code;
  $("#brief-title").textContent = item.title;
  $("#brief").textContent = type === "lesson" ? item.content : item.description;
  $("#submit").hidden = type !== "challenge";
  if (window.MXL) MXL.setLang(item.language || "python");
  $("#out").textContent = "La salida aparecerá aquí.";
  updateGutter();
  renderLists();
}

// --------------------------------------------------------------- Ejecución
function showResult(data, kind) {
  const { result } = data;
  const out = $("#out");
  out.replaceChildren();
  if (result.stdout) out.append(el("span", "", result.stdout));
  if (result.stderr) out.append(el("span", "err", (result.stdout ? "\n" : "") + result.stderr));
  if (!result.stdout && !result.stderr) out.append(el("span", "muted", "(sin salida)"));
  const meta = `código de salida ${result.exit_code} · ${result.duration_ms} ms`;
  if (kind === "submit") {
    const message = data.passed
      ? `Reto superado en ${data.attempts} intento(s)${data.points_awarded ? `, +${data.points_awarded} puntos` : ""}. `
      : `La salida no coincide con la esperada (intento ${data.attempts}). `;
    setStatus(message + meta, !data.passed);
  } else {
    setStatus(meta, !result.ok);
  }
  renderAudit(data.audit);
}

function renderAudit(report) {
  const box = $("#audit");
  box.className = "";
  box.replaceChildren();
  const score = el("div", "score");
  score.append(el("strong", "", String(report.score)), el("span", "", `nota ${report.grade}`));
  box.append(score);
  const m = report.metrics;
  box.append(el("p", "muted",
    `${m.sloc} líneas de código · ${m.functions} funciones · complejidad máx. ${m.max_complexity}`));
  if (!report.findings.length) box.append(el("p", "ok", "Sin hallazgos."));
  report.findings.forEach((f) => {
    box.append(el("div", `finding ${f.severity}`, `${f.line ? "L" + f.line + ": " : ""}${f.message}`));
  });
}

async function execute(kind) {
  if (kind === "run" && window.MXL && MXL.isBrowser()) return MXL.preview();
  if (!state.user) return setStatus("Inicia sesión para ejecutar código.", true);
  const code = $("#code").value;
  const current = state.current;
  $("#run").disabled = true;
  $("#submit").disabled = true;
  setStatus("Ejecutando…", false);
  try {
    const data = kind === "submit"
      ? await api(`/api/challenges/${current.slug}/submit`, { code })
      : await api("/api/run", {
          code, stdin: $("#stdin").value, language: $("#lang").value,
          lesson: current && current.type === "lesson" ? current.slug : null,
        });
    showResult(data, kind);
    await Promise.all([refreshMe(), loadHistory(), kind === "submit" ? loadLists() : null]);
  } catch (error) { setStatus(error.message, true); }
  finally { $("#run").disabled = false; $("#submit").disabled = false; }
}

// ---------------------------------------------------------------- Historial
async function loadHistory() {
  if (!state.user) return;
  const data = await api("/api/history");
  const box = $("#history");
  box.className = "";
  box.replaceChildren();
  data.lesson_progress.forEach((l) =>
    box.append(el("div", "hist-line", `${l.succeeded ? "✓" : "·"} ${l.title} (${l.runs} ejecuciones)`)));
  data.grades.forEach((g) =>
    box.append(el("div", "hist-line muted", `${g.grade} ${g.score} · ${g.origin}`)));
  if (!box.children.length) box.append(el("span", "muted", "Aún no hay actividad."));
}

// ------------------------------------------------------------------- Inicio
$("#code").addEventListener("input", updateGutter);
$("#code").addEventListener("scroll", () => { $("#gutter").scrollTop = $("#code").scrollTop; });
$("#code").addEventListener("keydown", onEditorKey);
$("#run").addEventListener("click", () => execute("run"));
$("#submit").addEventListener("click", () => execute("submit"));
$("#login").addEventListener("click", () => authenticate("login"));
$("#register").addEventListener("click", () => authenticate("register"));
$("#logout").addEventListener("click", logout);
$("#pass").addEventListener("keydown", (e) => { if (e.key === "Enter") authenticate("login"); });

(async () => {
  updateGutter();
  await refreshMe();
  await loadLists();
  await loadHistory();
})();
