const ui = {
  health: document.querySelector("#health"),
  hero: document.querySelector("#hero"),
  messages: document.querySelector("#messages"),
  form: document.querySelector("#chat-form"),
  input: document.querySelector("#message"),
  send: document.querySelector("#send"),
  newSession: document.querySelector("#new-session"),
  task: document.querySelector("#task-badge"),
  state: document.querySelector("#state-content"),
  trace: document.querySelector("#trace-content"),
  latency: document.querySelector("#latency"),
};

let sessionId = null;
let pendingMessage = null;

const labels = {
  origin: "Origin", destination: "Destination", travel_date: "Travel date",
  miles_required: "Miles required", cash_price: "Cash price", taxes: "Taxes & fees",
};

function node(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined) element.textContent = text;
  return element;
}

function futureDate() {
  const value = new Date();
  value.setDate(value.getDate() + 45);
  return value.toISOString().slice(0, 10);
}

async function request(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || `Request failed (${response.status})`);
  }
  return response.json();
}

async function startSession() {
  const data = await request("/api/sessions", { method: "POST" });
  sessionId = data.session_id;
  renderState(data.state);
}

function addMessage(role, content) {
  ui.hero.hidden = true;
  ui.messages.classList.add("active");
  const item = node("article", `message ${role}`);
  if (role === "assistant") item.append(node("div", "message-label", "ATLAS / ANSWER"));
  item.append(node("div", "message-text", content));
  ui.messages.append(item);
  ui.messages.scrollTop = ui.messages.scrollHeight;
  return item;
}

function showThinking() {
  const item = node("article", "message assistant");
  item.id = "thinking";
  item.append(node("div", "message-label", "ATLAS / WORKING"));
  const dots = node("div", "thinking");
  dots.append(node("i"), node("i"), node("i"));
  item.append(dots);
  ui.messages.append(item);
  return item;
}

function renderDecision(decision) {
  const article = node("article", "message assistant");
  article.append(node("div", "message-label", "ATLAS / STRUCTURED DECISION"));
  const card = node("div", "decision-card");
  const lead = node("div", "decision-lead");
  const copy = node("div");
  copy.append(node("p", "eyebrow", `RECOMMENDATION · ${decision.recommendation.confidence.toUpperCase()} CONFIDENCE`));
  const choice = decision.recommendation.choice === "award_ticket" ? "Use Avios" : decision.recommendation.choice === "cash_ticket" ? "Pay cash" : "It depends";
  copy.append(node("h3", "", choice), node("p", "", decision.recommendation.summary));
  const metric = node("div", "metric");
  metric.append(node("strong", "", `${decision.redemption_value.pence_per_mile}p`), node("small", "", "per Avios"));
  lead.append(copy, metric);
  card.append(lead);

  const grid = node("div", "decision-grid");
  const journeys = section("Train alternatives");
  if (decision.transport_options.length) {
    decision.transport_options.forEach((trip) => {
      const row = node("div", "journey");
      const time = `${String(trip.departure_local || "").slice(11,16)} → ${String(trip.arrival_local || "").slice(11,16)}`;
      row.append(node("strong", "", time), node("span", "", `${trip.duration_minutes || "—"} min · ${trip.transfers ?? "—"} transfer`));
      journeys.append(row);
    });
  } else journeys.append(node("p", "", "No scheduled journey was returned."));

  const benefits = section("Loyalty evidence");
  if (decision.loyalty_benefits.length) {
    decision.loyalty_benefits.forEach((item) => {
      const p = node("p");
      const link = node("a", "evidence-link", item.title);
      if (/^https:\/\//.test(item.source_url || "")) { link.href = item.source_url; link.target = "_blank"; link.rel = "noreferrer"; }
      p.append(link, document.createTextNode(` — ${item.benefit}`));
      benefits.append(p);
    });
  } else benefits.append(node("p", "", "No loyalty context was requested or verified."));

  const tradeoffs = section("Tradeoffs");
  tradeoffs.append(list(decision.tradeoffs));
  const limits = section("Limitations");
  limits.append(list(decision.limitations));
  grid.append(journeys, benefits, tradeoffs, limits);
  card.append(grid);
  article.append(card);
  ui.messages.append(article);
}

function section(title) { const el = node("section", "decision-section"); el.append(node("h4", "", title)); return el; }
function list(items) { const ul = node("ul"); (items || []).forEach((item) => ul.append(node("li", "", item))); return ul; }

function renderState(state) {
  ui.task.textContent = state.current_task || "general";
  ui.state.replaceChildren();
  const slots = Object.entries(state.slots || {});
  if (!slots.length) {
    ui.state.className = "empty-state";
    ui.state.append(node("div", "empty-orbit"), node("p", "", "参数将在用户明确提供后出现。"));
    return;
  }
  ui.state.className = "";
  slots.forEach(([name, info]) => {
    const item = node("div", "slot");
    const head = node("div", "slot-head");
    head.append(node("span", "slot-name", labels[name] || name), node("span", "slot-value", String(info.value)));
    item.append(head, node("div", "provenance", `User · turn ${info.turn_id} · “${info.source_text}”`));
    ui.state.append(item);
  });
  if ((state.missing_fields || []).length) ui.state.append(node("div", "missing", `Missing: ${state.missing_fields.map((x) => labels[x] || x).join(", ")}`));
}

function renderTrace(trace) {
  ui.latency.textContent = `${trace.duration_ms} ms`;
  ui.trace.className = "";
  ui.trace.replaceChildren();
  const summary = node("div", "trace-summary");
  [[trace.usage.total_tokens, "total tokens"], [trace.usage.requests, "model requests"]].forEach(([value, label]) => {
    const metric = node("div", "trace-metric"); metric.append(node("strong", "", String(value)), node("span", "", label)); summary.append(metric);
  });
  ui.trace.append(summary);
  if (trace.control_reason) {
    ui.trace.append(node("p", "provenance", `Deterministic control · ${trace.control_reason}`));
  }
  if (!trace.tools.length) ui.trace.append(node("p", "provenance", "No business tool was called."));
  trace.tools.forEach((tool, index) => {
    const step = node("div", "trace-step");
    step.append(node("h4", "", `${index + 1}. ${tool.tool}`), node("p", "", `status · ${tool.status}`));
    const details = node("details");
    details.append(node("summary", "", "Inspect arguments and output"), node("pre", "", JSON.stringify({ arguments: tool.arguments, output: tool.output }, null, 2)));
    step.append(details); ui.trace.append(step);
  });
}

async function sendMessage(message) {
  if (!sessionId) await startSession();
  addMessage("user", message);
  const thinking = showThinking();
  ui.send.disabled = true;
  try {
    const data = await request("/api/chat", { method: "POST", body: JSON.stringify({ session_id: sessionId, message }) });
    thinking.remove();
    if (data.answer_type === "decision") renderDecision(data.decision);
    else addMessage("assistant", data.message);
    renderState(data.state); renderTrace(data.trace);
  } catch (error) {
    thinking.remove(); addMessage("assistant", `暂时无法完成：${error.message}`);
  } finally {
    ui.send.disabled = false; ui.input.focus();
  }
}

ui.form.addEventListener("submit", (event) => {
  event.preventDefault(); const value = ui.input.value.trim(); if (!value) return;
  ui.input.value = ""; ui.input.style.height = "auto"; sendMessage(value);
});
ui.input.addEventListener("input", () => { ui.input.style.height = "auto"; ui.input.style.height = `${Math.min(ui.input.scrollHeight, 150)}px`; });
ui.input.addEventListener("keydown", (event) => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); ui.form.requestSubmit(); } });
document.querySelectorAll(".example").forEach((button) => button.addEventListener("click", () => { ui.input.value = button.dataset.prompt.replace("{{date}}", futureDate()); ui.input.focus(); }));
document.querySelectorAll(".tab").forEach((button) => button.addEventListener("click", () => {
  document.querySelectorAll(".tab,.tab-panel").forEach((item) => item.classList.remove("active"));
  button.classList.add("active"); document.querySelector(`#${button.dataset.tab}-panel`).classList.add("active");
}));
ui.newSession.addEventListener("click", async () => {
  if (sessionId) await request(`/api/sessions/${sessionId}`, { method: "DELETE" }).catch(() => null);
  sessionId = null; ui.messages.replaceChildren(); ui.messages.classList.remove("active"); ui.hero.hidden = false; ui.latency.textContent = "—";
  await startSession(); ui.input.focus();
});

Promise.all([request("/health"), startSession()]).then(([health]) => {
  ui.health.classList.add("ready"); ui.health.lastChild.textContent = health.api_key_configured ? " API ready" : " Key missing";
}).catch(() => { ui.health.lastChild.textContent = " Offline"; });
