const $ = (id) => document.getElementById(id);
let trip = null;
let conversationState = null;
let currentProposal = null;
let editingTransportId = null;
let editingStayId = null;
let editingStayAddress = null;
let activeImportBatch = null;
let importCandidateContext = null;

const CITY_TIMEZONES = {
  berlin:"Europe/Berlin", 柏林:"Europe/Berlin", cologne:"Europe/Berlin", köln:"Europe/Berlin", 科隆:"Europe/Berlin", frankfurt:"Europe/Berlin", 法兰克福:"Europe/Berlin", munich:"Europe/Berlin", 慕尼黑:"Europe/Berlin",
  paris:"Europe/Paris", 巴黎:"Europe/Paris", london:"Europe/London", 伦敦:"Europe/London", vienna:"Europe/Vienna", 维也纳:"Europe/Vienna", zurich:"Europe/Zurich", 苏黎世:"Europe/Zurich", rome:"Europe/Rome", 罗马:"Europe/Rome",
  madrid:"Europe/Madrid", 马德里:"Europe/Madrid", barcelona:"Europe/Madrid", 巴塞罗那:"Europe/Madrid", amsterdam:"Europe/Amsterdam", 阿姆斯特丹:"Europe/Amsterdam", brussels:"Europe/Brussels", 布鲁塞尔:"Europe/Brussels", monaco:"Europe/Monaco", 摩纳哥:"Europe/Monaco",
  beijing:"Asia/Shanghai", 北京:"Asia/Shanghai", shanghai:"Asia/Shanghai", 上海:"Asia/Shanghai", wuhan:"Asia/Shanghai", 武汉:"Asia/Shanghai",
  zhumadian:"Asia/Shanghai", 驻马店:"Asia/Shanghai", jinan:"Asia/Shanghai", 济南:"Asia/Shanghai", shenzhen:"Asia/Shanghai", 深圳:"Asia/Shanghai",
  "hong kong":"Asia/Hong_Kong", hongkong:"Asia/Hong_Kong", 香港:"Asia/Hong_Kong", "los angeles":"America/Los_Angeles", 洛杉矶:"America/Los_Angeles", "san francisco":"America/Los_Angeles", 旧金山:"America/Los_Angeles", 三藩市:"America/Los_Angeles",
  tokyo:"Asia/Tokyo", 东京:"Asia/Tokyo", osaka:"Asia/Tokyo", 大阪:"Asia/Tokyo", singapore:"Asia/Singapore", 新加坡:"Asia/Singapore",
  "new york":"America/New_York", 纽约:"America/New_York",
};
const TIMEZONES = [...new Set(["Asia/Shanghai", ...Object.values(CITY_TIMEZONES)])];

async function api(path, options = {}) {
  const formData = options.body instanceof FormData;
  const response = await fetch(path, { ...options, headers:{...(formData ? {} : {"Content-Type":"application/json"}), ...(options.headers || {})} });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(apiErrorMessage(body.detail,response.status));
  }
  return response.status === 204 ? null : response.json();
}

function apiErrorMessage(detail,status) {
  if (typeof detail === "string") return detail;
  const messages=Array.isArray(detail) ? detail.map((item)=>item?.msg || "").filter(Boolean) : [];
  if (messages.some((message)=>message.includes("arrival_at must be later than departure_at"))) {
    return "到达时间按所选时区早于出发时间。请核对到达日期和时区；跨越国际日期变更线时，到达日期通常会晚一天。";
  }
  if (messages.length) return messages.join("；").replace(/^Value error,\s*/i,"");
  return `请求失败：${status}`;
}

function toast(message) {
  $("toast").textContent = message;
  $("toast").classList.add("show");
  setTimeout(() => $("toast").classList.remove("show"), 2800);
}

function initializeTimezoneSelects() {
  for (const id of ["origin-timezone", "destination-timezone"]) {
    $(id).innerHTML = '<option value="">请选择时区</option>' + TIMEZONES.map((zone) => `<option value="${zone}">${zone}</option>`).join("");
  }
}
initializeTimezoneSelects();

$("create-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    trip = await api("/api/trips", { method:"POST", body:JSON.stringify({ title:$("trip-title").value, minimum_connection_minutes:Number($("min-connection").value) }) });
    $("create-view").classList.add("hidden");
    $("workspace").classList.remove("hidden");
    setDefaultTimezones();
    await Promise.all([render(), loadConversation(), loadImports()]);
    $("chat-input").focus();
  } catch (error) { toast(error.message); }
});

async function loadConversation() {
  conversationState = await api(`/api/trips/${trip.id}/conversation`);
  renderMessages(conversationState.messages || []);
  syncPendingReview();
  if (conversationState.ready_for_confirmation && !openCandidateDialog(conversationState.draft)) toast("候选状态异常，请继续补充信息后重试。");
}

$("chat-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const input = $("chat-input");
  const message = input.value.trim();
  const files = [...$("trip-files").files];
  if (!message && !files.length) return;
  input.value = "";
  renderPendingMessage(message || `上传 ${files.length} 个行程文件`);
  $("chat-send").disabled = true;
  try {
    if (files.length) {
      const body = new FormData();
      files.forEach((file) => body.append("files", file));
      body.append("instructions", message);
      activeImportBatch = await api(`/api/trips/${trip.id}/imports`, {method:"POST",body});
      $("trip-files").value=""; renderSelectedFiles();
      renderImportQueue();
      document.querySelector(".message.pending")?.remove();
      appendAssistantMessage(`已分析 ${activeImportBatch.documents.length} 个文件，识别出 ${activeImportBatch.candidates.length} 条候选。请在右侧逐条核对。`);
      return;
    }
    conversationState = await api(`/api/trips/${trip.id}/conversation`, { method:"POST", body:JSON.stringify({message}) });
    renderMessages(conversationState.messages);
    syncPendingReview();
    if (conversationState.ready_for_confirmation && !openCandidateDialog(conversationState.draft)) toast("候选状态异常，请继续补充信息后重试。");
  } catch (error) {
    renderMessages(conversationState?.messages || []);
    toast(error.message);
  } finally { $("chat-send").disabled = false; input.focus(); }
});

$("trip-files").addEventListener("change", renderSelectedFiles);
function renderSelectedFiles() {
  const files=[...$("trip-files").files];
  $("selected-files").classList.toggle("hidden",!files.length);
  $("selected-files").innerHTML=files.map((file)=>`<span class="file-chip">${escapeHtml(file.name)} · ${formatBytes(file.size)}</span>`).join("");
}
function formatBytes(value) { return value < 1024*1024 ? `${Math.ceil(value/1024)} KB` : `${(value/1024/1024).toFixed(1)} MB`; }
function appendAssistantMessage(message) {
  $("chat-messages").insertAdjacentHTML("beforeend",`<div class="message assistant"><span>TF</span><p>${escapeHtml(message)}</p></div>`);
  $("chat-messages").scrollTop=$("chat-messages").scrollHeight;
}

async function loadImports() {
  const batches=await api(`/api/trips/${trip.id}/imports`);
  activeImportBatch=batches.find((batch)=>batch.candidates.some((item)=>item.review_status === "pending")) || batches[0] || null;
  renderImportQueue();
}

function renderImportQueue() {
  const container=$("import-queue");
  if (!activeImportBatch) { container.classList.add("hidden"); container.innerHTML=""; return; }
  const all=activeImportBatch.candidates;
  const pending=all.filter((item)=>item.review_status === "pending");
  const done=all.length-pending.length;
  const failed=activeImportBatch.documents.filter((item)=>item.status === "failed");
  const failureNote=failed.length ? `<p class="missing-list">${failed.length} 个文件解析失败：${failed.map((item)=>`${escapeHtml(item.filename)}（${escapeHtml(item.error || "请重试")}）`).join("、")}</p>` : "";
  container.classList.remove("hidden");
  if (!pending.length) {
    container.innerHTML=`<div class="import-head"><div><p class="eyebrow">IMPORT COMPLETE</p><h3>本批次已核对完成</h3></div><span class="import-progress">${done}/${all.length}</span></div>${failureNote}`;
    return;
  }
  const candidate=pending[0];
  const item=candidate.kind === "transport" ? candidate.transport : candidate.stay;
  const title=candidate.kind === "transport" ? `${item.mode === "flight"?"航班":"火车"} · ${item.operator || "待补充运营商"}` : `住宿 · ${item.property_name || "待补充名称"}`;
  const detail=candidate.kind === "transport"
    ? `${escapeHtml(item.origin.city || "?")} → ${escapeHtml(item.destination.city || "?")}<br>${escapeHtml(item.departure_at || "?")} → ${escapeHtml(item.arrival_at || "?")}`
    : `${escapeHtml(item.city || "?")} · ${escapeHtml(item.check_in || "?")} → ${escapeHtml(item.check_out || "?")}`;
  const missing=candidate.missing_fields.length ? `<p class="missing-list">还需补全：${escapeHtml(candidate.missing_fields.map(missingLabel).join("、"))}</p>` : "";
  const duplicate=candidate.duplicate_of ? '<p class="duplicate-warning">疑似与本批次上一条候选重复</p>' : "";
  const primary=candidate.missing_fields.length ? "补全并确认" : candidate.duplicate_of ? "仍然添加" : "确认并加入";
  container.innerHTML=`<div class="import-head"><div><p class="eyebrow">IMPORT REVIEW</p><h3>逐条核对导入候选</h3></div><span class="import-progress">${done+1}/${all.length}</span></div>${failureNote}<article class="import-candidate"><h4>${escapeHtml(title)}</h4><p>${detail}</p>${missing}${duplicate}<small class="import-source">来源：${escapeHtml(candidate.source_filename)}${candidate.source_page?` · 第 ${candidate.source_page} 页`:""}${candidate.source_excerpt?` · “${escapeHtml(candidate.source_excerpt)}”`:""}</small><div class="import-actions"><button class="ghost mini" data-import-action="skip" data-id="${candidate.id}">跳过</button><button class="primary mini" data-import-action="confirm" data-id="${candidate.id}">${primary}</button></div></article>`;
}

function missingLabel(value) { return ({operator:"运营商","origin.name":"出发站/机场","origin.city":"出发城市","origin.timezone":"出发城市时区","destination.name":"到达站/机场","destination.city":"到达城市","destination.timezone":"到达城市时区",departure_at:"出发时间",arrival_at:"到达时间",property_name:"住宿名称",city:"住宿城市",check_in:"入住日期",check_out:"退房日期"})[value] || value; }

$("import-queue").addEventListener("click",async(event)=>{
  const button=event.target.closest("button[data-import-action]"); if(!button)return;
  const candidate=activeImportBatch.candidates.find((item)=>item.id===button.dataset.id); if(!candidate)return;
  if(button.dataset.importAction==="skip") {
    try {
      activeImportBatch=await api(`/api/trips/${trip.id}/imports/${activeImportBatch.id}/candidates/${candidate.id}/skip`,{method:"POST",body:"{}"});
      if(importCandidateContext?.candidateId===candidate.id) {
        importCandidateContext=null;
        candidate.kind==="transport" ? resetTransportForm() : resetStayForm();
      }
      renderImportQueue();
    } catch(error){toast(error.message);} return;
  }
  if(candidate.missing_fields.length) { fillImportCandidate(candidate); return; }
  button.disabled=true;
  try {
    const result=await api(`/api/trips/${trip.id}/imports/${activeImportBatch.id}/candidates/${candidate.id}/confirm`,{method:"POST",headers:{"If-Match":String(trip.version)},body:JSON.stringify({force_duplicate:Boolean(candidate.duplicate_of)})});
    trip=result.trip; activeImportBatch=result.batch; await render(); renderImportQueue(); toast("候选已加入行程。");
  } catch(error){await recoverVersion(error);button.disabled=false;}
});

function fillImportCandidate(candidate) {
  importCandidateContext={batchId:activeImportBatch.id,candidateId:candidate.id,kind:candidate.kind,forceDuplicate:Boolean(candidate.duplicate_of)};
  editingTransportId=null; editingStayId=null; editingStayAddress=null;
  $("manual-panel").open=true;
  if(candidate.kind==="transport") {
    const item=candidate.transport; $("manual-kind").value="transport"; $("manual-kind").dispatchEvent(new Event("change"));
    $("mode").value=item.mode; $("operator").value=item.operator||""; $("service-number").value=item.service_number||"";
    $("origin-city").value=item.origin.city||""; $("destination-city").value=item.destination.city||""; $("origin-name").value=item.origin.name||""; $("destination-name").value=item.destination.name||"";
    prepareCandidateTimezone("origin-city","origin-timezone",item.origin.timezone); prepareCandidateTimezone("destination-city","destination-timezone",item.destination.timezone);
    $("departure-at").value=localInput(item.departure_at); $("arrival-at").value=localInput(item.arrival_at); $("transport-submit").textContent="补全并确认导入";
  } else {
    const item=candidate.stay; $("manual-kind").value="stay"; $("manual-kind").dispatchEvent(new Event("change"));
    $("property-name").value=item.property_name||""; $("stay-city").value=item.city||""; $("check-in").value=item.check_in||""; $("check-out").value=item.check_out||""; $("stay-submit").textContent="补全并确认导入";
  }
  $("manual-panel").scrollIntoView({behavior:"smooth"});
}

$("chat-input").addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); $("chat-form").requestSubmit(); }
});

document.querySelectorAll("[data-message]").forEach((button) => button.addEventListener("click", () => {
  $("chat-input").value = button.dataset.message;
  $("chat-form").requestSubmit();
}));

$("reset-chat").addEventListener("click", async () => {
  try {
    conversationState = await api(`/api/trips/${trip.id}/conversation`, {method:"DELETE"});
    currentProposal = null;
    $("candidate-dialog").close();
    renderMessages([]);
    syncPendingReview();
    toast("已开始新任务，已确认行程不受影响。");
  } catch (error) { toast(error.message); }
});

function renderMessages(messages) {
  const welcome = `<div class="message assistant"><span>TF</span><p>你好，我可以帮你整理航班、火车和住宿。你现在掌握哪些信息？</p></div>`;
  $("chat-messages").innerHTML = welcome + messages.map((item) => `<div class="message ${item.role}"><span>${item.role === "user" ? "你" : "TF"}</span><p>${escapeHtml(item.content)}</p></div>`).join("");
  $("chat-messages").scrollTop = $("chat-messages").scrollHeight;
}

function renderPendingMessage(message) {
  const container = $("chat-messages");
  container.insertAdjacentHTML("beforeend", `<div class="message user"><span>你</span><p>${escapeHtml(message)}</p></div><div class="message assistant pending"><span>TF</span><p>正在整理…</p></div>`);
  container.scrollTop = container.scrollHeight;
}

function openCandidateDialog(proposal) {
  currentProposal = proposal;
  const cards = [];
  (proposal.provider_lookups || []).forEach((outcome) => {
    (outcome.candidates || []).forEach((item) => cards.push(renderProviderCandidate(item, outcome)));
  });
  (proposal.transports || []).forEach((item, index) => { if (!(item.missing_fields || []).length) cards.push(renderTransportCandidate(item, index)); });
  (proposal.stays || []).forEach((item, index) => { if (!(item.missing_fields || []).length) cards.push(renderStayCandidate(item, index)); });
  if (!cards.length) return false;
  $("candidate-content").innerHTML = cards.join("");
  if (!$("candidate-dialog").open) $("candidate-dialog").showModal();
  return true;
}

function syncPendingReview() {
  $("review-pending").classList.toggle("hidden", !conversationState?.ready_for_confirmation);
}

$("review-pending").addEventListener("click", () => {
  if (!openCandidateDialog(conversationState?.draft || {})) toast("当前没有可核对的完整候选。");
});

function renderProviderCandidate(item, outcome) {
  return `<article class="provider-outcome candidate-card"><div class="provider-head"><h3>${escapeHtml(item.operator)} ${escapeHtml(item.flight_number)}</h3><span class="live-badge">${escapeHtml(item.flight_status)}</span></div><p><b>${escapeHtml(item.origin.city)} → ${escapeHtml(item.destination.city)}</b></p><p>${escapeHtml(item.origin.name)} → ${escapeHtml(item.destination.name)}</p><p>${escapeHtml(zonedDisplay(item.departure_at,item.origin.timezone))} → ${escapeHtml(zonedDisplay(item.arrival_at,item.destination.timezone))}</p><small>联网时间 ${escapeHtml(item.checked_at)} · ${outcome.cached ? "缓存结果" : "实时请求"}</small><br><a class="attribution" href="${escapeHtml(outcome.attribution_url)}" target="_blank" rel="noreferrer">Flight data by AeroDataBox</a><button class="primary" data-confirm-kind="provider" data-candidate-id="${escapeHtml(item.candidate_id)}">确认并加入行程</button></article>`;
}

function renderTransportCandidate(item, index) {
  return `<article class="candidate-card"><h3>${item.mode === "flight" ? "航班" : "火车"} · ${escapeHtml(item.operator || "")}</h3><p><b>${escapeHtml(item.origin.city)} → ${escapeHtml(item.destination.city)}</b></p><p>${escapeHtml(item.origin.name)} → ${escapeHtml(item.destination.name)}</p><p>${escapeHtml(item.departure_at)} → ${escapeHtml(item.arrival_at)}</p><button class="primary" data-confirm-kind="transport" data-index="${index}">确认并加入行程</button></article>`;
}

function renderStayCandidate(item, index) {
  return `<article class="candidate-card"><h3>住宿 · ${escapeHtml(item.property_name)}</h3><p>${escapeHtml(item.city)} · ${escapeHtml(item.check_in)} → ${escapeHtml(item.check_out)}</p><button class="primary" data-confirm-kind="stay" data-index="${index}">确认并加入行程</button></article>`;
}

$("close-dialog").addEventListener("click", () => $("candidate-dialog").close());
$("candidate-content").addEventListener("click", async (event) => {
  const button = event.target.closest("button[data-confirm-kind]");
  if (!button) return;
  button.disabled = true;
  try {
    if (button.dataset.confirmKind === "provider") {
      trip = await api(`/api/trips/${trip.id}/providers/flights/confirm`, {method:"POST", headers:{"If-Match":String(trip.version)}, body:JSON.stringify({candidate_id:button.dataset.candidateId})});
    } else if (button.dataset.confirmKind === "transport") {
      const item = currentProposal.transports[Number(button.dataset.index)];
      trip = await api(`/api/trips/${trip.id}/transport`, {method:"POST",headers:{"If-Match":String(trip.version)},body:JSON.stringify(candidateTransportPayload(item))});
    } else {
      const item = currentProposal.stays[Number(button.dataset.index)];
      trip = await api(`/api/trips/${trip.id}/stays`, {method:"POST",headers:{"If-Match":String(trip.version)},body:JSON.stringify(candidateStayPayload(item))});
    }
    button.closest(".candidate-card").remove();
    await render();
    toast("已加入右侧确认行程。");
    if (!$("candidate-content").querySelector("button[data-confirm-kind]")) await finishConversationDraft();
  } catch (error) { await recoverVersion(error); button.disabled = false; }
});

async function finishConversationDraft() {
  conversationState = await api(`/api/trips/${trip.id}/conversation`, {method:"DELETE"});
  currentProposal = null;
  $("candidate-dialog").close();
  renderMessages([]);
  syncPendingReview();
}

function candidateTransportPayload(item) {
  return { mode:item.mode, operator:item.operator, service_number:item.service_number || null, origin:item.origin, destination:item.destination, departure_at:item.departure_at, arrival_at:item.arrival_at, source:{source_type:"text",source_excerpt:(item.source_excerpt || "conversation candidate").slice(0,500),confirmed_by_user:true} };
}
function candidateStayPayload(item) {
  return { property_name:item.property_name, city:item.city, address:item.address || null, check_in:item.check_in, check_out:item.check_out, source:{source_type:"text",source_excerpt:(item.source_excerpt || "conversation candidate").slice(0,500),confirmed_by_user:true} };
}

$("manual-kind").addEventListener("change", () => {
  const stay = $("manual-kind").value === "stay";
  $("transport-form").classList.toggle("hidden", stay);
  $("stay-form").classList.toggle("hidden", !stay);
});

for (const id of ["origin-city","destination-city"]) {
  $(id).addEventListener("input", () => {
    const prefix=id.startsWith("origin")?"origin":"destination";
    const timezone=$(`${prefix}-timezone`);
    timezone.dataset.manual="";
    if (!CITY_TIMEZONES[$(id).value.trim().toLowerCase()]) timezone.value="";
  });
  $(id).addEventListener("change", () => applyCityTimezone(id));
}
for (const id of ["origin-timezone","destination-timezone"]) $(id).addEventListener("change", () => { $(id).dataset.manual="1"; });
function applyCityTimezone(cityId) {
  const prefix = cityId.startsWith("origin") ? "origin" : "destination";
  const select = $(`${prefix}-timezone`);
  const zone = CITY_TIMEZONES[$(cityId).value.trim().toLowerCase()];
  if (zone) ensureTimezoneOption(`${prefix}-timezone`, zone);
  return Boolean(zone || (select.dataset.manual && select.value));
}
function prepareCandidateTimezone(cityId, timezoneId, provided) {
  const select=$(timezoneId);
  select.dataset.manual="";
  if (provided) { ensureTimezoneOption(timezoneId,provided); return; }
  const zone=CITY_TIMEZONES[$(cityId).value.trim().toLowerCase()];
  if (zone) ensureTimezoneOption(timezoneId,zone); else select.value="";
}
function ensureTimezoneOption(id, zone) {
  const select = $(id);
  if (![...select.options].some((option) => option.value === zone)) select.add(new Option(zone, zone));
  select.value = zone;
}
function setDefaultTimezones() {
  ensureTimezoneOption("origin-timezone", trip?.home_timezone || "Asia/Shanghai");
  ensureTimezoneOption("destination-timezone", trip?.home_timezone || "Asia/Shanghai");
  $("origin-timezone").dataset.manual=""; $("destination-timezone").dataset.manual="";
}

$("transport-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const originZoneReady=applyCityTimezone("origin-city"); const destinationZoneReady=applyCityTimezone("destination-city");
  if (!originZoneReady || !destinationZoneReady) { document.querySelector(".advanced-fields").open=true; toast("未识别该城市的时区，请在高级设置中点选后再提交。"); return; }
  const wasEditing = Boolean(editingTransportId);
  const originCity = $("origin-city").value.trim();
  const destinationCity = $("destination-city").value.trim();
  const payload = { mode:$("mode").value, operator:$("operator").value, service_number:$("service-number").value || null, origin:{name:$("origin-name").value || originCity,city:originCity,timezone:$("origin-timezone").value}, destination:{name:$("destination-name").value || destinationCity,city:destinationCity,timezone:$("destination-timezone").value}, departure_at:$("departure-at").value,arrival_at:$("arrival-at").value,source:{source_type:"form",source_excerpt:"user-confirmed compact form",confirmed_by_user:true} };
  try {
    if (importCandidateContext) {
      if (importCandidateContext.kind !== "transport") throw new Error("当前待补全候选是住宿，请切换回住宿表单。");
      const result=await confirmCompletedImport({transport:payload,force_duplicate:importCandidateContext.forceDuplicate});
      trip=result.trip; activeImportBatch=result.batch; importCandidateContext=null;
      resetTransportForm(); await render(); renderImportQueue(); toast("已补全并加入行程。");
    } else {
      trip = await saveTransport(payload); resetTransportForm(); await render(); toast(wasEditing ? "交通已更新。" : "交通已添加。");
    }
  } catch (error) { await recoverVersion(error); }
});

async function saveTransport(payload) {
  const path = editingTransportId ? `/api/trips/${trip.id}/transport/${editingTransportId}` : `/api/trips/${trip.id}/transport`;
  return api(path,{method:editingTransportId ? "PUT" : "POST",headers:{"If-Match":String(trip.version)},body:JSON.stringify(payload)});
}

$("stay-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const payload = {property_name:$("property-name").value,city:$("stay-city").value,address:editingStayAddress,check_in:$("check-in").value,check_out:$("check-out").value,source:{source_type:"form",source_excerpt:"user-confirmed compact form",confirmed_by_user:true}};
  try {
    if (importCandidateContext) {
      if (importCandidateContext.kind !== "stay") throw new Error("当前待补全候选是交通，请切换回交通表单。");
      const result=await confirmCompletedImport({stay:payload,force_duplicate:importCandidateContext.forceDuplicate});
      trip=result.trip; activeImportBatch=result.batch; importCandidateContext=null;
      resetStayForm(); await render(); renderImportQueue(); toast("已补全并加入行程。");
    } else {
      trip = await saveStay(payload); resetStayForm(); await render(); toast("住宿已保存。");
    }
  } catch (error) { await recoverVersion(error); }
});

async function confirmCompletedImport(payload) {
  const context=importCandidateContext;
  if (!context) throw new Error("导入候选已失效，请重新选择。");
  return api(`/api/trips/${trip.id}/imports/${context.batchId}/candidates/${context.candidateId}/confirm`,{method:"POST",headers:{"If-Match":String(trip.version)},body:JSON.stringify(payload)});
}

async function saveStay(payload) {
  const path = editingStayId ? `/api/trips/${trip.id}/stays/${editingStayId}` : `/api/trips/${trip.id}/stays`;
  return api(path,{method:editingStayId ? "PUT" : "POST",headers:{"If-Match":String(trip.version)},body:JSON.stringify(payload)});
}

document.querySelectorAll("[data-rule-question]").forEach((button) => button.addEventListener("click", () => {
  $("rule-question").value=button.dataset.ruleQuestion;
  if ($("rule-reservation").value) $("rules-form").requestSubmit();
  else toast("请先选择一段已确认交通行程。");
}));

$("rules-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button=$("rules-submit");
  button.disabled=true; button.textContent="正在检索…";
  try {
    const result=await api(`/api/trips/${trip.id}/rules/query`,{method:"POST",body:JSON.stringify({reservation_id:$("rule-reservation").value || null,question:$("rule-question").value.trim()})});
    renderRuleAnswer(result);
  } catch(error) { toast(error.message); }
  finally { button.disabled=false; button.textContent="检索官方规则"; }
});

function renderRuleAnswer(result) {
  const container=$("rules-result");
  const labels={answered:"已基于证据回答",needs_clarification:"需要补充信息",out_of_scope:"当前范围不支持",insufficient_evidence:"证据不足",stale_evidence:"来源需要复核",unavailable:"服务暂时不可用"};
  const warning=result.status!=="answered";
  const citations=(result.citations || []).map((item)=>{
    const url=safeExternalUrl(item.source_url);
    const title=url ? `<a href="${escapeHtml(url)}" target="_blank" rel="noreferrer">${escapeHtml(item.title)}</a>` : escapeHtml(item.title);
    return `<li>${title} · ${escapeHtml(item.source_name)} · 抓取于 ${escapeHtml(item.retrieved_at)}<br>${escapeHtml(item.excerpt)}</li>`;
  }).join("");
  const limitations=(result.limitations || []).map((item)=>`<p class="rule-limitations">限制：${escapeHtml(item)}</p>`).join("");
  container.classList.remove("hidden");
  container.innerHTML=`<span class="rule-status ${warning?"warning":""}">${escapeHtml(labels[result.status] || result.status)}</span><h4>${escapeHtml(result.answer)}</h4>${citations?`<ol class="rule-citations">${citations}</ol>`:""}${limitations}<p class="rule-limitations">检索证据：${escapeHtml((result.retrieved_evidence_ids || []).join(", ") || "无")} · ${escapeHtml(result.latency_ms)} ms</p>`;
  container.scrollIntoView({behavior:"smooth",block:"nearest"});
}

function safeExternalUrl(value) {
  try { const url=new URL(value); return url.protocol==="https:" ? url.toString() : ""; }
  catch (_) { return ""; }
}

$("timeline").addEventListener("click", async (event) => {
  const button = event.target.closest("button[data-action]");
  if (!button) return;
  const item = trip.reservations.find((candidate) => candidate.id === button.dataset.id);
  if (!item) return;
  if (button.dataset.action === "rules") {
    $("rule-reservation").value=item.id;
    $("rule-question").focus();
    $("rules-panel")?.scrollIntoView({behavior:"smooth",block:"center"});
    return;
  }
  if (button.dataset.action === "edit") { fillManualForm(item); return; }
  if (button.dataset.action === "delete" && window.confirm("确定删除这条已确认行程吗？")) {
    try { trip = await api(`/api/trips/${trip.id}/reservations/${item.id}`,{method:"DELETE",headers:{"If-Match":String(trip.version)}}); await render(); toast("已删除。"); } catch (error) { await recoverVersion(error); }
  }
});

function fillManualForm(item) {
  importCandidateContext=null;
  editingTransportId=null; editingStayId=null; editingStayAddress=null;
  $("manual-panel").open = true;
  if (item.kind === "transport") {
    $("manual-kind").value="transport"; $("manual-kind").dispatchEvent(new Event("change")); editingTransportId=item.id;
    $("mode").value=item.mode; $("operator").value=item.operator; $("service-number").value=item.service_number || "";
    $("origin-city").value=item.origin.city; $("destination-city").value=item.destination.city; $("origin-name").value=item.origin.name; $("destination-name").value=item.destination.name;
    ensureTimezoneOption("origin-timezone",item.origin.timezone); ensureTimezoneOption("destination-timezone",item.destination.timezone);
    $("origin-timezone").dataset.manual="1"; $("destination-timezone").dataset.manual="1";
    $("departure-at").value=localInput(item.departure_at); $("arrival-at").value=localInput(item.arrival_at); $("transport-submit").textContent="保存交通修改";
  } else {
    $("manual-kind").value="stay"; $("manual-kind").dispatchEvent(new Event("change")); editingStayId=item.id; editingStayAddress=item.address;
    $("property-name").value=item.property_name; $("stay-city").value=item.city; $("check-in").value=item.check_in; $("check-out").value=item.check_out; $("stay-submit").textContent="保存住宿修改";
  }
  $("manual-panel").scrollIntoView({behavior:"smooth"});
}

function resetTransportForm() { editingTransportId=null; $("transport-form").reset(); setDefaultTimezones(); $("transport-submit").textContent="确认添加交通"; }
function resetStayForm() { editingStayId=null; editingStayAddress=null; $("stay-form").reset(); $("stay-submit").textContent="确认添加住宿"; }
function localInput(value) { return value ? value.slice(0,16) : ""; }

async function recoverVersion(error) {
  if (error.message.includes("current version")) { trip=await api(`/api/trips/${trip.id}`); await render(); toast("行程已有更新，已刷新，请再次确认。"); } else toast(error.message);
}

async function render() {
  $("active-title").textContent=trip.title; $("ics-link").href=`/api/trips/${trip.id}/calendar.ics`;
  const items=[...trip.reservations].sort((a,b)=>startOf(a).localeCompare(startOf(b)));
  $("timeline").innerHTML=items.length ? items.map(renderItem).join("") : '<p class="empty">尚无已确认行程。与左侧 Agent 对话，或使用下方快捷添加。</p>';
  updateRuleReservations(items);
  const conflicts=await api(`/api/trips/${trip.id}/conflicts`);
  const conflictLabels={schedule_overlap:"时间重叠",short_connection:"换乘时间不足"};
  $("conflicts").innerHTML=conflicts.length ? conflicts.map((item)=>`<div class="conflict"><strong>${escapeHtml(conflictLabels[item.type] || item.type)}</strong><br>${escapeHtml(item.message)}</div>`).join("") : '<p class="no-conflicts">当前未发现交通时间冲突</p>';
}
function updateRuleReservations(items) {
  const select=$("rule-reservation");
  const previous=select.value;
  const transports=items.filter((item)=>item.kind==="transport" && item.status==="confirmed");
  select.innerHTML=transports.length ? transports.map((item)=>`<option value="${escapeHtml(item.id)}">${escapeHtml(item.operator)} ${escapeHtml(item.service_number || "")} · ${escapeHtml(item.origin.city)} → ${escapeHtml(item.destination.city)}</option>`).join("") : '<option value="">请先添加交通行程</option>';
  if (transports.some((item)=>item.id===previous)) select.value=previous;
}
function startOf(item) { return item.kind === "transport" ? item.departure_at : item.check_in; }
function renderItem(item) {
  const askRules=item.kind === "transport" ? `<button class="mini ghost" data-action="rules" data-id="${item.id}">问规则</button>` : "";
  const actions=`<div class="item-actions">${askRules}<button class="mini ghost" data-action="edit" data-id="${item.id}">编辑</button><button class="mini ghost danger" data-action="delete" data-id="${item.id}">删除</button></div>`;
  if (item.kind === "stay") return `<article class="trip-item"><div class="trip-time">${escapeHtml(item.check_in)}</div><div class="trip-main"><strong>${escapeHtml(item.property_name)}</strong><span>${escapeHtml(item.city)} · ${escapeHtml(item.check_in)} → ${escapeHtml(item.check_out)}</span></div><div class="trip-meta"><span class="badge">USER CONFIRMED · R${item.revision}</span>${actions}</div></article>`;
  const provider=Object.values(item.provenance || {}).some((source)=>source.source_type === "provider");
  return `<article class="trip-item"><div class="trip-time">${escapeHtml(zonedDisplay(item.departure_at,item.origin.timezone))}</div><div class="trip-main"><strong>${escapeHtml(item.origin.city)} → ${escapeHtml(item.destination.city)}</strong><span>${escapeHtml(item.origin.name)} → ${escapeHtml(item.destination.name)} · ${escapeHtml(item.operator)} ${escapeHtml(item.service_number || "")}</span></div><div class="trip-meta">${escapeHtml(zonedDisplay(item.arrival_at,item.destination.timezone,false))}<br><span class="badge">${provider ? "PROVIDER CHECKED · " : ""}USER CONFIRMED · R${item.revision}</span>${actions}</div></article>`;
}
function zonedDisplay(value,timeZone,includeDate=true) { try { return new Intl.DateTimeFormat("zh-CN",{timeZone,...(includeDate?{month:"short",day:"numeric"}:{}),hour:"2-digit",minute:"2-digit",hour12:false}).format(new Date(value)); } catch (_) { return value; } }
function escapeHtml(value) { return String(value ?? "").replace(/[&<>"']/g,(character)=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#039;"})[character]); }
