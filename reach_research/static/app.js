const LABELS = {web:"Web",news:"ニュース",x:"X",reddit:"Reddit",youtube:"YouTube",github:"GitHub",instagram:"Instagram",threads:"Threads",tiktok:"TikTok",facebook:"Facebook"};
const DEFAULT_SOURCES = Object.keys(LABELS);
const $ = id => document.getElementById(id);
let currentJob = sessionStorage.getItem("reach-job") || null;
let currentResult = null;
let filter = "all";

function authHeaders() {
  const token = sessionStorage.getItem("reach-token");
  return token ? {Authorization: `Bearer ${token}`} : {};
}

async function api(path, options = {}) {
  const response = await fetch(path, {...options, headers: {...authHeaders(), ...options.headers}});
  if (!response.ok) {
    let detail = `HTTP ${response.status}`;
    try { const body = await response.json(); detail = typeof body.detail === "string" ? body.detail : detail; } catch (_) {}
    const error = new Error(detail);
    error.status = response.status;
    throw error;
  }
  return response.json();
}

function showAccess() {
  $("access-panel").hidden = false;
  $("access-toggle").setAttribute("aria-expanded", "true");
  $("access-token").focus();
}

function showError(message) {
  $("error-state").hidden = false;
  $("error-detail").textContent = message;
}

function unreadReason(row) {
  const error = row.error || "";
  if (error.includes("CERTIFICATE_VERIFY_FAILED")) return "ページの証明書を検証できず、本文は未確認です。";
  if (error.includes("could not be verified")) return "対象ページの本文を確認できませんでした。";
  if (error.includes("timed out")) return "ページの取得が時間切れになりました。";
  return error || "本文は未確認です。検索結果のみを記録しています。";
}

function renderSources(sources) {
  const container = $("sources");
  container.replaceChildren();
  sources.forEach(source => {
    const label = document.createElement("label");
    label.className = "source-option";
    const input = document.createElement("input");
    input.type = "checkbox";
    input.name = "source";
    input.value = source;
    input.checked = true;
    input.addEventListener("change", updateSourceCount);
    const span = document.createElement("span");
    span.textContent = LABELS[source] || source;
    label.append(input, span);
    container.append(label);
  });
  updateSourceCount();
}

function updateSourceCount() {
  const count = document.querySelectorAll('input[name="source"]:checked').length;
  $("source-count").textContent = `${count}媒体`;
}

async function loadCapabilities() {
  try {
    const info = await api("/api/capabilities");
    renderSources(info.sources);
    $("scrapling").disabled = !info.diagnostics.scrapling_installed;
    $("scrapling-note").textContent = info.diagnostics.scrapling_installed ? "本文の取得に使用します" : "この環境では利用できません";
  } catch (error) {
    if (error.status === 401) showAccess();
    else showError(`接続状態を確認できません: ${error.message}`);
  }
}

function setView(view) {
  $("empty-state").hidden = view !== "empty";
  $("job-state").hidden = view !== "job";
  $("result-state").hidden = view !== "result";
  $("error-state").hidden = view !== "error";
}

async function submitResearch(event) {
  event.preventDefault();
  const sources = [...document.querySelectorAll('input[name="source"]:checked')].map(input => input.value);
  if (!sources.length) { setView("error"); showError("対象媒体を1つ以上選択してください。"); return; }
  const payload = {theme: $("theme").value.trim(), sources, depth: $("depth").value,
                   limit: Number($("limit").value), max_pages: 20, use_scrapling: $("scrapling").checked};
  if (payload.theme.length < 2) { setView("error"); showError("テーマを2文字以上で入力してください。"); return; }
  $("submit-button").disabled = true;
  setView("job");
  $("job-title").textContent = "調査を準備しています";
  $("job-detail").textContent = "媒体ごとの検索を開始します。";
  try {
    const job = await api("/api/research", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(payload)});
    currentJob = job.id;
    sessionStorage.setItem("reach-job", job.id);
    pollJob(job.id);
  } catch (error) {
    $("submit-button").disabled = false;
    setView("error");
    showError(error.message);
    if (error.status === 401) showAccess();
  }
}

async function pollJob(jobId) {
  if (jobId !== currentJob) return;
  try {
    const job = await api(`/api/jobs/${jobId}`);
    if (job.status === "complete") {
      currentResult = await api(`/api/jobs/${jobId}/result`);
      $("submit-button").disabled = false;
      renderResult(currentResult);
      return;
    }
    if (job.status === "failed") {
      $("submit-button").disabled = false;
      setView("error");
      showError(job.error || "調査中にエラーが発生しました。");
      return;
    }
    setView("job");
    $("job-title").textContent = job.status === "running" ? "WebとSNSを調査中" : "調査の順番を待っています";
    $("job-detail").textContent = `「${job.theme}」の資料を集めています。画面を開いたままお待ちください。`;
    setTimeout(() => pollJob(jobId), 2500);
  } catch (error) {
    if (error.status === 401) { showAccess(); return; }
    if (error.status === 404) { sessionStorage.removeItem("reach-job"); currentJob = null; setView("empty"); return; }
    $("job-detail").textContent = "接続を確認しています…";
    setTimeout(() => pollJob(jobId), 4000);
  }
}

function renderResult(result) {
  setView("result");
  $("result-theme").textContent = result.theme;
  const depthLabels = {quick:"クイック", balanced:"標準", deep:"詳細"};
  $("result-meta").textContent = `${new Date(result.created_at).toLocaleString("ja-JP")} · ${depthLabels[result.depth] || "標準"}調査`;
  const coverage = Object.entries(result.coverage);
  $("count-found").textContent = coverage.reduce((sum, [, state]) => sum + state.discovered, 0);
  $("count-read").textContent = coverage.reduce((sum, [, state]) => sum + state.read, 0);
  const list = $("coverage");
  list.replaceChildren();
  coverage.forEach(([source, state]) => {
    const item = document.createElement("div"); item.className = `coverage-item ${state.status}`;
    const name = document.createElement("span"); name.className = "name"; name.textContent = LABELS[source] || source;
    const counts = document.createElement("span"); counts.className = "counts";
    counts.textContent = state.status === "error" ? "取得エラー" : `発見 ${state.discovered} / 確認 ${state.read}`;
    item.title = state.note || "";
    item.append(name, counts); list.append(item);
  });
  renderEvidence();
}

function renderEvidence() {
  const list = $("evidence"); list.replaceChildren();
  const rows = (currentResult?.results || []).filter(row => filter === "all" || row.status === filter);
  if (!rows.length) { const empty = document.createElement("p"); empty.className = "empty-results"; empty.textContent = "この条件に該当する資料はありません。"; list.append(empty); return; }
  rows.forEach(row => {
    const card = document.createElement("article"); card.className = "evidence-item";
    const top = document.createElement("div"); top.className = "evidence-top";
    const badge = document.createElement("span"); badge.className = `badge ${row.status}`;
    badge.textContent = row.status === "read" ? "本文確認" : "発見のみ";
    const source = document.createElement("span"); source.className = "source-badge"; source.textContent = LABELS[row.source] || row.source;
    top.append(badge, source);
    const link = document.createElement("a"); link.textContent = row.page_title || row.title || row.url;
    try { const url = new URL(row.url); if (["http:","https:"].includes(url.protocol)) link.href = url.href; } catch (_) {}
    link.target = "_blank"; link.rel = "noopener noreferrer";
    const detail = document.createElement("p");
    detail.textContent = row.status === "read" ? (row.text || "").replace(/\s+/g, " ").slice(0, 180) : unreadReason(row);
    card.append(top, link, detail); list.append(card);
  });
}

async function download(kind) {
  if (!currentJob) return;
  const suffix = kind === "md" ? "report" : "result";
  try {
    const response = await fetch(`/api/jobs/${currentJob}/${suffix}`, {headers:authHeaders()});
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a"); link.href = url; link.download = `agent-reach-${currentJob.slice(0,8)}.${kind}`;
    document.body.append(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  } catch (error) { showError(`保存できませんでした: ${error.message}`); }
}

renderSources(DEFAULT_SOURCES);
$("research-form").addEventListener("submit", submitResearch);
$("select-all").addEventListener("click", () => { document.querySelectorAll('input[name="source"]').forEach(input => input.checked = true); updateSourceCount(); });
$("select-none").addEventListener("click", () => { document.querySelectorAll('input[name="source"]').forEach(input => input.checked = false); updateSourceCount(); });
$("access-toggle").addEventListener("click", () => { const panel = $("access-panel"); panel.hidden = !panel.hidden; $("access-toggle").setAttribute("aria-expanded", String(!panel.hidden)); });
$("access-save").addEventListener("click", () => { sessionStorage.setItem("reach-token", $("access-token").value.trim()); $("access-token").value = ""; $("access-panel").hidden = true; loadCapabilities(); if (currentJob) pollJob(currentJob); });
$("download-md").addEventListener("click", () => download("md"));
$("download-json").addEventListener("click", () => download("json"));
document.querySelectorAll("[data-filter]").forEach(button => button.addEventListener("click", () => { filter = button.dataset.filter; document.querySelectorAll("[data-filter]").forEach(tab => tab.classList.toggle("active", tab === button)); renderEvidence(); }));
loadCapabilities();
if (currentJob) pollJob(currentJob);
