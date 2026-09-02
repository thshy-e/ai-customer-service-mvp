const state = { token: localStorage.getItem("sales_token") || "", role: localStorage.getItem("sales_role") || "", email: localStorage.getItem("sales_email") || "", interview: null, lastDraft: null };
const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];

async function api(path, options = {}) {
  const headers = new Headers(options.headers || {});
  if (state.token) headers.set("Authorization", `Bearer ${state.token}`);
  const response = await fetch(path, { ...options, headers });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.detail || "请求失败，请稍后重试");
  return payload;
}

function showView(name) {
  $$(".sales-view").forEach((view) => view.classList.toggle("is-active", view.dataset.view === name));
  $$(".nav-item").forEach((item) => item.classList.toggle("is-active", item.dataset.viewTarget === name));
  const title = { overview: "工作概览", knowledge: "企业资料", inquiry: "询盘解析", drafts: "销售草稿" }[name] || "工作概览";
  $("#page-title").textContent = title;
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function openLogin() {
  const dialog = $("#login-dialog");
  if (!dialog.open) dialog.showModal();
}

function refreshIdentity() {
  $("#user-email").textContent = state.email || "未登录";
  $("#user-role").textContent = state.role === "admin" ? "管理员" : state.role === "sales" ? "业务员" : "—";
}

function renderJob(job) {
  const node = $("#knowledge-job");
  node.hidden = false;
  node.innerHTML = `<strong>${job.status === "succeeded" || job.status === "succeeded_with_warnings" ? "解析完成" : job.status === "failed" ? "解析失败" : "正在解析"}</strong><br>${job.message}<br><span>${job.page_count ? `识别到 ${job.page_count} 页` : "正在读取页面"}</span>`;
  $("#metric-sources").textContent = job.status.startsWith("succeeded") ? "1" : "0";
}

async function pollJob(jobId) {
  for (let attempt = 0; attempt < 60; attempt += 1) {
    const job = await api(`/api/knowledge/jobs/${jobId}`);
    renderJob(job);
    if (["succeeded", "succeeded_with_warnings", "failed"].includes(job.status)) return;
    await new Promise((resolve) => setTimeout(resolve, 800));
  }
}

function renderAnalysis(result) {
  const inquiry = result.inquiry;
  const draft = result.draft;
  state.lastDraft = draft;
  const tags = (items) => items.length ? `<div class="tag-list">${items.map((item) => `<span class="analysis-tag">${escapeHtml(item)}</span>`).join("")}</div>` : `<strong class="muted-value">未知</strong>`;
  $("#analysis-result").innerHTML = `<section class="analysis-card"><div class="analysis-card-heading"><h3>客户需求摘要</h3><span class="confidence">置信度 ${Math.round(inquiry.confidence * 100)}%</span></div><div class="analysis-fields"><div class="analysis-field"><span>客户国家</span><strong>${escapeHtml(inquiry.customer_country)}</strong></div><div class="analysis-field"><span>客户公司</span><strong>${escapeHtml(inquiry.customer_company)}</strong></div><div class="analysis-field"><span>数量</span><strong>${escapeHtml(inquiry.quantity)}</strong></div><div class="analysis-field"><span>应用场景</span><strong>${escapeHtml(inquiry.application)}</strong></div><div class="analysis-field"><span>已知信息</span>${tags(inquiry.known_requirements)}</div><div class="analysis-field"><span>待确认信息</span>${tags(inquiry.missing_requirements)}</div></div></section><section class="draft-card"><div class="draft-card-heading"><h3>${escapeHtml(draft.subject)}</h3><button class="copy-button" type="button" id="copy-draft">复制英文草稿 ↗</button></div><div class="draft-body" id="draft-body">${escapeHtml(draft.body)}</div>${draft.warnings.length ? `<ul class="warning-list">${draft.warnings.map((warning) => `<li>${escapeHtml(warning)}</li>`).join("")}</ul>` : ""}<div class="source-list">${draft.cited_facts.length ? draft.cited_facts.map((source) => `<span class="source-chip">来源 · ${escapeHtml(source.label)}</span>`).join("") : `<span class="source-chip">暂无产品事实来源，需人工确认</span>`}</div></section>`;
  $("#copy-draft").addEventListener("click", async () => {
    await navigator.clipboard.writeText(draft.body);
    $("#copy-draft").textContent = "已复制 ✓";
  });
}

function escapeHtml(value) { return String(value ?? "").replace(/[&<>'"]/g, (character) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" }[character])); }

async function init() {
  refreshIdentity();
  if (!state.token) openLogin();
  $$("[data-view-target]").forEach((button) => button.addEventListener("click", () => { showView(button.dataset.viewTarget); if (button.dataset.startInterview) startInterview(); }));
  $("#logout-button").addEventListener("click", () => { localStorage.removeItem("sales_token"); state.token = ""; openLogin(); });
  $("#login-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const error = $("#login-error"); error.textContent = "";
    try {
      const result = await api("/api/auth/login", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ email: $("#login-email").value, password: $("#login-password").value }) });
      state.token = result.token; state.email = result.email; state.role = result.role;
      localStorage.setItem("sales_token", state.token); localStorage.setItem("sales_email", state.email); localStorage.setItem("sales_role", state.role);
      refreshIdentity(); $("#login-dialog").close();
    } catch (err) { error.textContent = err.message; }
  });
  $("#knowledge-file").addEventListener("change", (event) => { const file = event.target.files[0]; if (file) $("#knowledge-file-name").textContent = file.name; });
  $("#knowledge-upload").addEventListener("click", async () => {
    const file = $("#knowledge-file").files[0]; if (!file) return $("#knowledge-file-name").textContent = "请先选择一个文件";
    const form = new FormData(); form.append("file", file);
    try { const result = await api("/api/knowledge/sources", { method: "POST", body: form }); renderJob({ status: "queued", message: "上传完成，任务已排队", page_count: 0 }); await pollJob(result.job_id); } catch (err) { renderJob({ status: "failed", message: err.message, page_count: 0 }); }
  });
  $("#inquiry-file").addEventListener("change", (event) => { const file = event.target.files[0]; if (file) $("#inquiry-file-label").textContent = file.name; });
  $("#analyze-inquiry").addEventListener("click", async () => {
    const file = $("#inquiry-file").files[0]; if (!file) return $("#inquiry-file-label").textContent = "请先选择一张截图";
    const form = new FormData(); form.append("file", file); form.append("message", $("#inquiry-context").value);
    $("#analysis-loading").hidden = false;
    try { renderAnalysis(await api("/api/inquiries/analyze", { method: "POST", body: form })); } catch (err) { $("#analysis-result").innerHTML = `<div class="empty-result"><span class="empty-mark">!</span><h3>分析失败</h3><p>${escapeHtml(err.message)}</p></div>`; } finally { $("#analysis-loading").hidden = true; }
  });
  $("#start-interview").addEventListener("click", startInterview);
  $("#interview-submit").addEventListener("click", submitInterviewAnswer);
}

async function startInterview() {
  showView("knowledge");
  $("#interview-panel").hidden = false;
  try { state.interview = await api("/api/company/interviews", { method: "POST" }); renderInterview(); } catch (err) { $("#interview-status").textContent = err.message; }
}

function renderInterview() { const interview = state.interview; $("#interview-progress").textContent = `${interview.question_index} / ${interview.total_questions}`; $("#interview-question").textContent = interview.question || "十问已完成，等待管理员发布企业画像"; $("#interview-status").textContent = interview.status === "complete" ? "可提交审核" : "草稿状态"; $("#interview-submit").textContent = interview.status === "complete" ? "已完成" : "保存并继续"; }

async function submitInterviewAnswer() {
  if (!state.interview || state.interview.status === "complete") return;
  const answer = $("#interview-answer").value.trim(); if (!answer) return;
  try { state.interview = await api(`/api/company/interviews/${state.interview.session_id}/answers`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ question: state.interview.question, answer }) }); $("#interview-answer").value = ""; renderInterview(); } catch (err) { $("#interview-status").textContent = err.message; }
}

window.addEventListener("DOMContentLoaded", init);
