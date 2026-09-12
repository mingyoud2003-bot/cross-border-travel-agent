const $ = (id) => document.getElementById(id);
let trip = null;
let proposalText = "";
let proposalQueue = [];
let editingTransportId = null;
let editingStayId = null;

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(typeof body.detail === "string" ? body.detail : `Request failed: ${response.status}`);
  }
  return response.status === 204 ? null : response.json();
}

function toast(message) {
  $("toast").textContent = message;
  $("toast").classList.add("show");
  setTimeout(() => $("toast").classList.remove("show"), 2800);
}

$("create-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    trip = await api("/api/trips", {
      method: "POST",
      body: JSON.stringify({
        title: $("trip-title").value,
        minimum_connection_minutes: Number($("min-connection").value),
      }),
    });
    $("create-view").classList.add("hidden");
    $("workspace").classList.remove("hidden");
    await render();
  } catch (error) { toast(error.message); }
});

$("sample").addEventListener("click", () => {
  $("quick-text").value = "请查询 2026-09-12 的 LH400 航班状态";
});

$("parse-text").addEventListener("click", async () => {
  const button = $("parse-text");
  proposalText = $("quick-text").value.trim();
  if (!proposalText) return toast("请先输入行程文字。");
  button.disabled = true;
  try {
    const proposal = await api(`/api/trips/${trip.id}/proposals/text`, {
      method: "POST", body: JSON.stringify({ text: proposalText }),
    });
    proposalQueue = [
      ...proposal.transports.map((item) => ({ kind: "transport", item })),
      ...proposal.stays.map((item) => ({ kind: "stay", item })),
    ];
    renderProviderLookups(proposal.provider_lookups || []);
    if (proposalQueue.length) {
      showNextProposal();
    } else if ((proposal.provider_lookups || []).length) {
      showProposalMessages(proposal);
    } else if ((proposal.flight_lookups || []).some((item) => item.missing_fields.length)) {
      showProposalMessages(proposal);
    } else {
      toast("没有识别到可用的交通或住宿信息。");
    }
  } catch (error) { toast(error.message); }
  finally { button.disabled = false; }
});

function showNextProposal() {
  const candidate = proposalQueue.shift();
  if (!candidate) return;
  if (candidate.kind === "transport") fillTransport(candidate.item);
  else fillStay(candidate.item);
  showProposalNote(candidate.item.missing_fields);
}

function showProposalMessages(proposal) {
  const missing = (proposal.flight_lookups || []).flatMap((item) => item.missing_fields || []);
  const messages = [
    ...(proposal.clarification_questions || []),
    ...(missing.length ? [`航班查询还缺少：${[...new Set(missing)].join("、")}`] : []),
    ...(proposal.warnings || []),
  ];
  $("proposal-note").textContent = messages.join(" ") || "联网结果仅作为候选，请核对后确认。";
  $("proposal-note").classList.remove("hidden");
}

function renderProviderLookups(outcomes) {
  const container = $("provider-results");
  if (!outcomes.length) {
    container.innerHTML = "";
    container.classList.add("hidden");
    return;
  }
  container.innerHTML = outcomes.map((outcome) => {
    const candidates = (outcome.candidates || []).map(renderFlightCandidate).join("");
    const cacheNote = outcome.cached ? " · 缓存结果" : "";
    return `<section class="provider-outcome">
      <div class="provider-head"><strong>${escapeHtml(outcome.flight_number)} · ${escapeHtml(outcome.departure_date)}</strong><span>${escapeHtml(outcome.status.toUpperCase())}${cacheNote}</span></div>
      <p>${escapeHtml(outcome.message)}</p>${candidates}
      <a class="attribution" href="${escapeHtml(outcome.attribution_url)}" target="_blank" rel="noreferrer">Flight data by AeroDataBox</a>
    </section>`;
  }).join("");
  container.classList.remove("hidden");
}

function renderFlightCandidate(item) {
  const departure = zonedDisplay(item.departure_at, item.origin.timezone);
  const arrival = zonedDisplay(item.arrival_at, item.destination.timezone);
  const details = [
    item.departure_terminal ? `出发航站楼 ${item.departure_terminal}` : "",
    item.departure_gate ? `登机口 ${item.departure_gate}` : "",
    item.arrival_terminal ? `到达航站楼 ${item.arrival_terminal}` : "",
  ].filter(Boolean).join(" · ");
  return `<article class="flight-candidate">
    <div><span class="live-badge">${escapeHtml(item.flight_status)}</span><strong>${escapeHtml(item.operator)} ${escapeHtml(item.flight_number)}</strong></div>
    <p>${escapeHtml(item.origin.name)} → ${escapeHtml(item.destination.name)}</p>
    <p>${escapeHtml(departure)} → ${escapeHtml(arrival)}</p>
    ${details ? `<small>${escapeHtml(details)}</small>` : ""}
    <button class="primary confirm-provider" type="button" data-candidate-id="${escapeHtml(item.candidate_id)}">确认并加入行程</button>
  </article>`;
}

$("provider-results").addEventListener("click", async (event) => {
  const button = event.target.closest("button[data-candidate-id]");
  if (!button) return;
  button.disabled = true;
  try {
    trip = await api(`/api/trips/${trip.id}/providers/flights/confirm`, {
      method: "POST",
      headers: { "If-Match": String(trip.version) },
      body: JSON.stringify({ candidate_id: button.dataset.candidateId }),
    });
    await render();
    button.closest(".flight-candidate").remove();
    toast("已保存经 AeroDataBox 查询且由你确认的航班。");
  } catch (error) {
    await recoverVersion(error);
    button.disabled = false;
  }
});

function showProposalNote(missingFields) {
  const missing = missingFields.length ? missingFields.join("、") : "无";
  const remaining = proposalQueue.length ? `保存后还有 ${proposalQueue.length} 条待确认。` : "";
  $("proposal-note").textContent = `Agent 只已填入候选值；缺失：${missing}。请核对后手动确认。${remaining}`;
  $("proposal-note").classList.remove("hidden");
}

$("transport-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const payload = {
    mode: $("mode").value,
    operator: $("operator").value,
    service_number: $("service-number").value || null,
    origin: { name: $("origin-name").value, city: $("origin-city").value, timezone: $("origin-timezone").value },
    destination: { name: $("destination-name").value, city: $("destination-city").value, timezone: $("destination-timezone").value },
    departure_at: $("departure-at").value,
    arrival_at: $("arrival-at").value,
    source: { source_type: proposalText ? "text" : "form", source_excerpt: proposalText.slice(0, 500), confirmed_by_user: true },
  };
  try {
    const path = editingTransportId ? `/api/trips/${trip.id}/transport/${editingTransportId}` : `/api/trips/${trip.id}/transport`;
    trip = await api(path, {
      method: editingTransportId ? "PUT" : "POST",
      headers: { "If-Match": String(trip.version) }, body: JSON.stringify(payload),
    });
    resetTransportForm(proposalQueue.length === 0);
    await render();
    if (proposalQueue.length) showNextProposal();
    toast("已保存用户确认的交通信息。");
  } catch (error) { await recoverVersion(error); }
});

$("stay-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const payload = {
    property_name: $("property-name").value, city: $("stay-city").value,
    address: $("stay-address").value || null, check_in: $("check-in").value,
    check_out: $("check-out").value,
    source: { source_type: proposalText ? "text" : "form", source_excerpt: proposalText.slice(0, 500), confirmed_by_user: true },
  };
  try {
    const path = editingStayId ? `/api/trips/${trip.id}/stays/${editingStayId}` : `/api/trips/${trip.id}/stays`;
    trip = await api(path, {
      method: editingStayId ? "PUT" : "POST",
      headers: { "If-Match": String(trip.version) }, body: JSON.stringify(payload),
    });
    resetStayForm(proposalQueue.length === 0);
    await render();
    if (proposalQueue.length) showNextProposal();
    toast("已保存用户确认的住宿信息。");
  } catch (error) { await recoverVersion(error); }
});

$("timeline").addEventListener("click", async (event) => {
  const button = event.target.closest("button[data-action]");
  if (!button) return;
  const item = trip.reservations.find((candidate) => candidate.id === button.dataset.id);
  if (!item) return;
  if (button.dataset.action === "edit") {
    if (item.kind === "transport") fillTransport(item, true); else fillStay(item, true);
    document.querySelector(".input-panel").scrollIntoView({ behavior: "smooth" });
    return;
  }
  if (button.dataset.action === "delete" && window.confirm("确定从行程中删除这一项吗？")) {
    try {
      trip = await api(`/api/trips/${trip.id}/reservations/${item.id}`, {
        method: "DELETE", headers: { "If-Match": String(trip.version) },
      });
      await render();
      toast("已删除。");
    } catch (error) { await recoverVersion(error); }
  }
});

async function recoverVersion(error) {
  if (error.message.includes("current version")) {
    trip = await api(`/api/trips/${trip.id}`);
    await render();
    toast("行程已在其他页面更新，已刷新最新版本，请再次确认。");
  } else toast(error.message);
}

function fillTransport(item, editing = false) {
  $("mode").value = item.mode; $("operator").value = item.operator || "";
  $("service-number").value = item.service_number || "";
  $("origin-name").value = item.origin.name || ""; $("origin-city").value = item.origin.city || "";
  $("origin-timezone").value = item.origin.timezone || "";
  $("destination-name").value = item.destination.name || ""; $("destination-city").value = item.destination.city || "";
  $("destination-timezone").value = item.destination.timezone || "";
  $("departure-at").value = localInput(item.departure_at); $("arrival-at").value = localInput(item.arrival_at);
  editingTransportId = editing ? item.id : null;
  $("transport-submit").textContent = editing ? "保存修改" : "确认并加入行程";
}

function fillStay(item, editing = false) {
  $("property-name").value = item.property_name || ""; $("stay-city").value = item.city || "";
  $("stay-address").value = item.address || ""; $("check-in").value = item.check_in || "";
  $("check-out").value = item.check_out || ""; editingStayId = editing ? item.id : null;
  $("stay-submit").textContent = editing ? "保存修改" : "确认并加入行程";
}

function resetTransportForm(finished = true) {
  if (finished) proposalText = "";
  editingTransportId = null; $("transport-form").reset();
  $("origin-timezone").value = "Europe/Berlin"; $("destination-timezone").value = "Europe/Berlin";
  $("transport-submit").textContent = "确认并加入行程"; $("proposal-note").classList.add("hidden");
}
function resetStayForm(finished = true) {
  if (finished) proposalText = "";
  editingStayId = null; $("stay-form").reset();
  $("stay-submit").textContent = "确认并加入行程"; $("proposal-note").classList.add("hidden");
}
function localInput(value) { return value ? value.slice(0, 16) : ""; }

async function render() {
  $("active-title").textContent = trip.title; $("ics-link").href = `/api/trips/${trip.id}/calendar.ics`;
  const items = [...trip.reservations].sort((a, b) => startOf(a).localeCompare(startOf(b)));
  $("timeline").innerHTML = items.length ? items.map(renderItem).join("") : '<p class="empty">还没有行程。可以用一句话填充，或直接使用表单。</p>';
  const conflicts = await api(`/api/trips/${trip.id}/conflicts`);
  $("conflicts").innerHTML = conflicts.map((item) => `<div class="conflict"><strong>${escapeHtml(item.type)}</strong><br>${escapeHtml(item.message)}</div>`).join("");
}

function startOf(item) { return item.kind === "transport" ? item.departure_at : item.check_in; }
function renderItem(item) {
  const actions = `<div class="item-actions"><button class="mini" data-action="edit" data-id="${item.id}">编辑</button><button class="mini danger" data-action="delete" data-id="${item.id}">删除</button></div>`;
  if (item.kind === "stay") return `<article class="trip-item"><div class="trip-time">${escapeHtml(item.check_in)}</div><div class="trip-main"><strong>${escapeHtml(item.property_name)}</strong><span>${escapeHtml(item.city)} · ${escapeHtml(item.check_in)} → ${escapeHtml(item.check_out)}</span></div><div class="trip-meta"><span class="badge">USER CONFIRMED · R${item.revision}</span>${actions}</div></article>`;
  const departure = zonedDisplay(item.departure_at, item.origin.timezone);
  const arrival = zonedDisplay(item.arrival_at, item.destination.timezone, false);
  const providerVerified = Object.values(item.provenance || {}).some((source) => source.source_type === "provider");
  const badge = providerVerified ? "PROVIDER CHECKED · USER CONFIRMED" : "USER CONFIRMED";
  return `<article class="trip-item"><div class="trip-time">${escapeHtml(departure)}</div><div class="trip-main"><strong>${escapeHtml(item.origin.name)} → ${escapeHtml(item.destination.name)}</strong><span>${escapeHtml(item.operator)} ${escapeHtml(item.service_number || "")} · ${escapeHtml(item.origin.timezone)} → ${escapeHtml(item.destination.timezone)}</span></div><div class="trip-meta">${escapeHtml(arrival)}<br><span class="badge">${badge} · R${item.revision}</span>${providerVerified ? '<br><a class="attribution" href="https://aerodatabox.com/" target="_blank" rel="noreferrer">Flight data by AeroDataBox</a>' : ""}${actions}</div></article>`;
}
function zonedDisplay(value, timeZone, includeDate = true) {
  try {
    return new Intl.DateTimeFormat("zh-CN", { timeZone, ...(includeDate ? { month: "short", day: "numeric" } : {}), hour: "2-digit", minute: "2-digit", hour12: false }).format(new Date(value));
  } catch (_) { return value; }
}
function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (character) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;" })[character]);
}
