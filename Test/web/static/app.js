// WeldEvent — 智能标注工作流（全部 20 个接口）

const API = "";

// ===== DOM =====
const $ = (id) => document.getElementById(id);
const datasetSelect = $("datasetSelect");
const datasetInfo = $("datasetInfo");
const datasetName = $("datasetName");
const datasetImageCount = $("datasetImageCount");
const deleteDatasetBtn = $("deleteDatasetBtn");
const imageListArea = $("imageListArea");
const imageList = $("imageList");
const newDatasetName = $("newDatasetName");
const uploadArea = $("uploadArea");
const fileInput = $("fileInput");
const uploadPreview = $("uploadPreview");
const createDatasetBtn = $("createDatasetBtn");
const requirementInput = $("requirementInput");
const designBtn = $("designBtn");
const executeBtn = $("executeBtn");
const redesignBtn = $("redesignBtn");
const workflowContent = $("workflowContent");
const workflowDesc = $("workflowDesc");
const resultContent = $("resultContent");
const resultDesc = $("resultDesc");
const jobTaskPanel = $("jobTaskPanel");
const jobList = $("jobList");
const taskList = $("taskList");
const agentLog = $("agentLog");
const llmReport = $("llmReport");
const llmReportBody = $("llmReportBody");
const llmReportClose = $("llmReportClose");
const connStatus = $("connStatus");

let currentTemplate = null;
let selectedDatasetId = null;
let selectedDatasetName = "";
let uploadedFiles = [];
// 执行后缓存的 ID
let lastJobId = null;
let lastTaskId = null;

// ===== 工具函数 =====

function setConnStatus(state, text) {
  const dot = connStatus.querySelector(".dot");
  const label = connStatus.querySelector(".conn-text");
  dot.className = `dot dot-${state}`;
  label.textContent = text;
}

function setLoading(btn, loading) {
  btn.disabled = loading;
  const spinner = btn.querySelector(".btn-spinner");
  if (spinner) spinner.hidden = !loading;
  const text = btn.querySelector(".btn-text");
  if (text && loading) text.textContent = "设计中...";
}

function escapeHtml(s) {
  const div = document.createElement("div");
  div.textContent = s;
  return div.innerHTML;
}

function expandStep(stepId) {
  const step = $(stepId);
  step.classList.remove("step-collapsed");
  const body = step.querySelector(".step-body");
  if (body) body.hidden = false;
}

function markStepDone(stepId) {
  const step = $(stepId);
  step.classList.add("step-done");
}

function logAgent(msg) {
  const ts = new Date().toLocaleTimeString();
  agentLog.innerHTML += `<div class="agent-log-entry"><span class="agent-log-ts">${ts}</span> ${escapeHtml(msg)}</div>`;
  agentLog.scrollTop = agentLog.scrollHeight;
}

// ===== Step 1: 数据准备 =====

// 1. list_datasets
async function loadDatasets() {
  setConnStatus("idle", "连接中...");
  try {
    const resp = await fetch(`${API}/api/datasets`);
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.detail || "失败");

    const records = data.records || [];
    datasetSelect.innerHTML = "";
    if (records.length === 0) {
      datasetSelect.innerHTML = '<option value="">暂无数据集，请新建</option>';
      setConnStatus("ok", "已连接 · 无数据集");
    } else {
      datasetSelect.innerHTML = '<option value="">请选择数据集</option>';
      records.forEach((ds) => {
        const opt = document.createElement("option");
        opt.value = ds.id;
        opt.textContent = ds.name || ds.id;
        datasetSelect.appendChild(opt);
      });
      setConnStatus("ok", `已连接 · ${records.length} 个数据集`);
    }
  } catch (e) {
    setConnStatus("err", "连接失败");
    datasetSelect.innerHTML = '<option value="">连接失败</option>';
    console.error(e);
  }
}

// 选择数据集 → 3. get_dataset + 5. list_dataset_images
datasetSelect.addEventListener("change", async () => {
  selectedDatasetId = datasetSelect.value;
  selectedDatasetName = datasetSelect.selectedOptions[0]?.textContent || "";

  if (!selectedDatasetId) {
    datasetInfo.hidden = true;
    imageListArea.hidden = true;
    designBtn.disabled = true;
    return;
  }

  try {
    // 3. get_dataset + 5. list_dataset_images
    const [dsResp, imgResp] = await Promise.all([
      fetch(`${API}/api/datasets/${selectedDatasetId}`),
      fetch(`${API}/api/datasets/${selectedDatasetId}/images`),
    ]);
    const dsData = await dsResp.json();
    const imgData = await imgResp.json();

    datasetName.textContent = dsData.name || selectedDatasetId;
    const imgCount = imgData.total || (imgData.records || []).length;
    datasetImageCount.textContent = `${imgCount} 张图片`;
    datasetInfo.hidden = false;
    designBtn.disabled = false;

    // 显示图片列表
    const images = imgData.records || [];
    if (images.length > 0) {
      imageListArea.hidden = false;
      imageList.innerHTML = images.map(img =>
        `<div class="image-item">${escapeHtml(img.filename || img.id)}</div>`
      ).join("");
    } else {
      imageListArea.hidden = true;
    }
  } catch (e) {
    datasetInfo.hidden = true;
    designBtn.disabled = false;
  }
});

// 4. delete_dataset
deleteDatasetBtn.addEventListener("click", async () => {
  if (!selectedDatasetId) return;
  if (!confirm(`确定删除数据集「${selectedDatasetName}」？`)) return;

  try {
    const resp = await fetch(`${API}/api/datasets/${selectedDatasetId}`, { method: "DELETE" });
    if (!resp.ok) throw new Error("删除失败");
    selectedDatasetId = null;
    selectedDatasetName = "";
    datasetInfo.hidden = true;
    imageListArea.hidden = true;
    designBtn.disabled = true;
    await loadDatasets();
  } catch (e) {
    alert("删除失败: " + e.message);
  }
});

// 上传区域交互
uploadArea.addEventListener("click", () => fileInput.click());
uploadArea.addEventListener("dragover", (e) => { e.preventDefault(); uploadArea.classList.add("drag-over"); });
uploadArea.addEventListener("dragleave", () => uploadArea.classList.remove("drag-over"));
uploadArea.addEventListener("drop", (e) => { e.preventDefault(); uploadArea.classList.remove("drag-over"); handleFiles(e.dataTransfer.files); });
fileInput.addEventListener("change", () => handleFiles(fileInput.files));

function handleFiles(fileList) {
  uploadedFiles = Array.from(fileList).filter(f => f.type.startsWith("image/"));
  if (uploadedFiles.length === 0) return;

  uploadPreview.hidden = false;
  uploadPreview.innerHTML = "";
  uploadedFiles.slice(0, 6).forEach(f => {
    const thumb = document.createElement("div");
    thumb.className = "thumb";
    const img = document.createElement("img");
    img.src = URL.createObjectURL(f);
    thumb.appendChild(img);
    uploadPreview.appendChild(thumb);
  });
  if (uploadedFiles.length > 6) {
    const more = document.createElement("div");
    more.className = "thumb thumb-more";
    more.textContent = `+${uploadedFiles.length - 6}`;
    uploadPreview.appendChild(more);
  }
  createDatasetBtn.disabled = !newDatasetName.value.trim();
}

newDatasetName.addEventListener("input", () => {
  createDatasetBtn.disabled = !(newDatasetName.value.trim() && uploadedFiles.length > 0);
});

// 2. create_dataset + 6. upload_dataset_images
createDatasetBtn.addEventListener("click", async () => {
  const name = newDatasetName.value.trim();
  if (!name || uploadedFiles.length === 0) return;

  createDatasetBtn.disabled = true;
  createDatasetBtn.textContent = "创建中...";

  try {
    // 2. create_dataset
    const createResp = await fetch(`${API}/api/datasets`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, modality: "IMAGE" }),
    });
    const createData = await createResp.json();
    if (!createResp.ok) throw new Error(createData.detail || "创建失败");

    const newId = createData.id;
    selectedDatasetId = newId;
    selectedDatasetName = name;

    // 6. upload_dataset_images（真实文件 multipart）
    const formData = new FormData();
    uploadedFiles.forEach(f => formData.append("files", f));
    await fetch(`${API}/api/datasets/${newId}/images`, { method: "POST", body: formData });

    datasetName.textContent = name;
    datasetImageCount.textContent = `${uploadedFiles.length} 张图片`;
    datasetInfo.hidden = false;
    designBtn.disabled = false;
    markStepDone("stepData");
    await loadDatasets();
    datasetSelect.value = newId;
  } catch (e) {
    alert("创建数据集失败: " + e.message);
  } finally {
    createDatasetBtn.disabled = false;
    createDatasetBtn.textContent = "创建并上传";
  }
});

// ===== Step 2: 标注需求 =====

document.querySelectorAll(".tag").forEach(tag => {
  tag.addEventListener("click", () => {
    const text = tag.dataset.text;
    const current = requirementInput.value.trim();
    requirementInput.value = current ? `${current}，${text}` : text;
  });
});

// /api/design（高级路由，非 20 个之一）
designBtn.addEventListener("click", async () => {
  if (!selectedDatasetId) return alert("请先选择或创建数据集");
  const requirement = requirementInput.value.trim();
  if (!requirement) return alert("请输入标注需求");

  setLoading(designBtn, true);
  expandStep("stepWorkflow");
  workflowDesc.textContent = "LLM 设计中...";
  workflowContent.innerHTML = '<div class="loading-state"><div class="spinner"></div><p>LLM 正在分析需求并设计工作流...</p></div>';

  try {
    const resp = await fetch(`${API}/api/design`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ requirement, dataset_id: selectedDatasetId, dataset_name: selectedDatasetName }),
    });
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.detail || "设计失败");

    currentTemplate = data.template;
    renderWorkflow(data.template);
    workflowDesc.textContent = `LLM 已设计 ${data.template.control_points?.length || 0} 个控制点的工作流`;
    markStepDone("stepRequirement");
  } catch (e) {
    workflowContent.innerHTML = `<div class="error-state"><p>设计失败: ${escapeHtml(e.message)}</p></div>`;
    workflowDesc.textContent = "设计失败";
  } finally {
    setLoading(designBtn, false);
  }
});

// ===== Step 3: 工作流渲染 =====

function renderWorkflow(tpl) {
  const cps = tpl.control_points || [];
  const transitions = tpl.transitions || [];
  const transitionMap = {};
  transitions.forEach(t => {
    if (!transitionMap[t.from_cp]) transitionMap[t.from_cp] = [];
    (t.branches || []).forEach(b => transitionMap[t.from_cp].push(b));
  });

  let html = `<div class="wf-overview">`;
  html += `<div class="wf-stat"><span class="wf-stat-num">${cps.length}</span><span class="wf-stat-label">控制点</span></div>`;
  html += `<div class="wf-stat"><span class="wf-stat-num">${transitions.length}</span><span class="wf-stat-label">转移</span></div>`;
  html += `<div class="wf-stat"><span class="wf-stat-num">${tpl.entry_point || "-"}</span><span class="wf-stat-label">入口</span></div>`;
  html += `</div><div class="wf-flow">`;

  cps.forEach((cp) => {
    const params = cp.params || {};
    const labels = params.labels || [];
    const binding = cp.activity_binding || {};

    html += `<div class="wf-node"><div class="wf-node-marker"></div><div class="wf-node-body">`;
    html += `<div class="wf-node-header"><span class="wf-node-name">${escapeHtml(cp.name || cp.id)}</span>`;
    html += `<span class="wf-node-activity">${escapeHtml(binding.activity_name || "")}</span></div>`;
    html += `<div class="wf-node-params">`;
    if (params.dataset_id) html += `<div class="wf-param"><span class="wf-param-key">数据集</span><span class="wf-param-val">${escapeHtml(params.dataset_id.slice(-8))}</span></div>`;
    if (params.job_name) html += `<div class="wf-param"><span class="wf-param-key">作业名</span><span class="wf-param-val">${escapeHtml(params.job_name)}</span></div>`;
    if (labels.length > 0) html += `<div class="wf-param"><span class="wf-param-key">标签</span><div class="wf-labels">${labels.map(l => `<span class="wf-label">${escapeHtml(l)}</span>`).join("")}</div></div>`;
    html += `</div></div></div>`;

    const nexts = transitionMap[cp.id] || [];
    if (nexts.length > 0) {
      html += `<div class="wf-arrow">`;
      nexts.forEach(n => { html += `<span class="wf-arrow-label">${escapeHtml(n.condition || "default")} → ${escapeHtml(n.to_cp)}</span>`; });
      html += `</div>`;
    }
  });
  html += `</div>`;
  workflowContent.innerHTML = html;
}

// /api/execute（高级路由：内部调 7.create_job + 12.create_task + 16.trigger_agent）
executeBtn.addEventListener("click", async () => {
  if (!currentTemplate) return;

  executeBtn.disabled = true;
  executeBtn.textContent = "执行中...";
  expandStep("stepResult");
  resultDesc.textContent = "正在提交到标注平台...";
  resultContent.innerHTML = '<div class="loading-state"><div class="spinner"></div><p>创建作业并触发标注...</p></div>';

  try {
    const resp = await fetch(`${API}/api/execute`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ template: currentTemplate }),
    });
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.detail || "执行失败");

    renderResults(data.results || []);
    markStepDone("stepWorkflow");

    // 缓存 job/task ID
    const okResults = (data.results || []).filter(r => r.status === "OK" && r.data);
    if (okResults.length > 0) {
      lastJobId = okResults[0].data.job_id;
      lastTaskId = okResults[0].data.task_id;
    }

    // 显示作业/任务管理面板
    jobTaskPanel.hidden = false;

    if (data.summary) {
      llmReportBody.textContent = data.summary;
      llmReport.hidden = false;
    }

    const okCount = okResults.length;
    resultDesc.textContent = `${okCount}/${data.results.length} 个控制点已提交`;

    // 自动加载作业和任务列表
    await loadJobs();
    await loadTasks();
  } catch (e) {
    resultContent.innerHTML = `<div class="error-state"><p>执行失败: ${escapeHtml(e.message)}</p></div>`;
    resultDesc.textContent = "执行失败";
  } finally {
    executeBtn.disabled = false;
    executeBtn.textContent = "执行工作流";
  }
});

function renderResults(results) {
  let html = "";
  results.forEach(r => {
    const isOk = r.status === "OK";
    html += `<div class="result-card ${isOk ? 'result-ok' : 'result-err'}">`;
    html += `<div class="result-card-header"><span class="result-card-name">${escapeHtml(r.name || r.cp_id)}</span>`;
    html += `<span class="result-card-status ${isOk ? 'status-ok' : 'status-err'}">${isOk ? '已提交' : '失败'}</span></div>`;
    if (r.data) {
      html += `<div class="result-card-body">`;
      if (r.data.job_id) html += `<div class="result-row"><span>作业</span><span>${escapeHtml(r.data.job_id)}</span></div>`;
      if (r.data.task_id) html += `<div class="result-row"><span>任务</span><span>${escapeHtml(r.data.task_id)}</span></div>`;
      if (r.data.assignee) html += `<div class="result-row"><span>标注员</span><span>${escapeHtml(r.data.assignee)}</span></div>`;
      if (r.data.assign_status) html += `<div class="result-row"><span>分配状态</span><span class="result-msg">${escapeHtml(r.data.assign_status)}</span></div>`;
      if (r.data.agent_status) html += `<div class="result-row"><span>Agent启动</span><span class="result-msg">${escapeHtml(r.data.agent_status)}</span></div>`;
      if (r.data.ai_status) html += `<div class="result-row"><span>AI标注</span><span class="result-msg">${escapeHtml(r.data.ai_status)}</span></div>`;
      if (r.data.message) html += `<div class="result-row"><span>流程</span><span class="result-msg">${escapeHtml(r.data.message)}</span></div>`;
      html += `</div>`;
    }
    if (r.error) html += `<div class="result-card-error">${escapeHtml(r.error)}</div>`;
    html += `</div>`;
  });
  resultContent.innerHTML = html;
}

redesignBtn.addEventListener("click", () => {
  currentTemplate = null;
  workflowDesc.textContent = "等待 LLM 设计...";
  resultDesc.textContent = "等待执行...";
  designBtn.disabled = false;
  designBtn.querySelector(".btn-text").textContent = "让 LLM 设计工作流";
});

// ===== Step 4: 作业/任务/Agent 管理 =====

// 8. list_jobs
async function loadJobs() {
  if (!selectedDatasetId) return;
  try {
    const resp = await fetch(`${API}/api/datasets/${selectedDatasetId}/jobs`);
    const data = await resp.json();
    const records = data.records || [];
    if (records.length === 0) {
      jobList.innerHTML = '<div class="empty-hint">暂无作业</div>';
      return;
    }
    jobList.innerHTML = records.map(j =>
      `<div class="list-item" data-id="${escapeHtml(j.id)}">
        <span class="list-item-name">${escapeHtml(j.name || j.id)}</span>
        <span class="list-item-meta">${escapeHtml(j.status || '')} · ${escapeHtml(j.annotationType || '')}</span>
      </div>`
    ).join("");
    // 缓存第一个 job ID
    if (!lastJobId && records.length > 0) lastJobId = records[0].id;
  } catch (e) {
    jobList.innerHTML = `<div class="empty-hint">加载失败</div>`;
  }
}

$("listJobsBtn").addEventListener("click", loadJobs);

// 9. get_job（刷新作业状态）
$("refreshJobBtn").addEventListener("click", async () => {
  if (!lastJobId) return alert("暂无作业");
  try {
    const resp = await fetch(`${API}/api/jobs/${lastJobId}`);
    const data = await resp.json();
    alert(`作业状态: ${data.status}\n完成: ${data.completedCount || 0}/${data.totalCount || '?'}`);
  } catch (e) {
    alert("查询失败: " + e.message);
  }
});

// 10. list_tasks
async function loadTasks() {
  if (!lastJobId) return;
  try {
    const resp = await fetch(`${API}/api/jobs/${lastJobId}/tasks`);
    const data = await resp.json();
    const records = data.records || [];
    if (records.length === 0) {
      taskList.innerHTML = '<div class="empty-hint">暂无任务</div>';
      return;
    }
    taskList.innerHTML = records.map(t =>
      `<div class="list-item" data-id="${escapeHtml(t.id)}">
        <span class="list-item-name">${escapeHtml(t.id)}</span>
        <span class="list-item-meta">${escapeHtml(t.status || '')}</span>
      </div>`
    ).join("");
    if (!lastTaskId && records.length > 0) lastTaskId = records[0].id;
  } catch (e) {
    taskList.innerHTML = `<div class="empty-hint">加载失败</div>`;
  }
}

// 11. list_all_tasks
$("listAllTasksBtn").addEventListener("click", async () => {
  try {
    const resp = await fetch(`${API}/api/tasks?page=1&size=20`);
    const data = await resp.json();
    const records = data.records || [];
    if (records.length === 0) {
      taskList.innerHTML = '<div class="empty-hint">暂无任务</div>';
      return;
    }
    taskList.innerHTML = records.map(t =>
      `<div class="list-item" data-id="${escapeHtml(t.id)}">
        <span class="list-item-name">${escapeHtml(t.id)}</span>
        <span class="list-item-meta">${escapeHtml(t.status || '')}</span>
      </div>`
    ).join("");
    if (!lastTaskId && records.length > 0) lastTaskId = records[0].id;
  } catch (e) {
    taskList.innerHTML = `<div class="empty-hint">加载失败</div>`;
  }
});

// 12. create_task
$("createTaskBtn").addEventListener("click", async () => {
  if (!lastJobId) return alert("暂无作业，无法创建任务");
  try {
    const resp = await fetch(`${API}/api/tasks`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ jobId: lastJobId }),
    });
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.detail || "创建失败");
    lastTaskId = data.id || data.taskId;
    logAgent(`创建任务成功: ${lastTaskId}`);
    await loadTasks();
  } catch (e) {
    alert("创建任务失败: " + e.message);
  }
});

// 13. get_task — 点击任务列表项查看详情
taskList.addEventListener("click", async (e) => {
  const item = e.target.closest(".list-item");
  if (!item) return;
  const taskId = item.dataset.id;
  try {
    const resp = await fetch(`${API}/api/tasks/${taskId}`);
    const data = await resp.json();
    alert(`任务详情\nID: ${data.id}\n状态: ${data.status}\n分配: ${data.assigneeId || '无'}`);
  } catch (e) {
    alert("查询失败: " + e.message);
  }
});

// 14. assign_task
jobList.addEventListener("contextmenu", async (e) => {
  e.preventDefault();
  const item = e.target.closest(".list-item");
  if (!item) return;
  const jobId = item.dataset.id;
  // 先查任务
  try {
    const resp = await fetch(`${API}/api/jobs/${jobId}/tasks`);
    const data = await resp.json();
    const tasks = data.records || [];
    if (tasks.length === 0) return alert("该作业暂无任务");
    const taskId = tasks[0].id;
    const assignResp = await fetch(`${API}/api/tasks/${taskId}/assign`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ taskId, assigneeId: "annotator1", assigneeName: "标注员1" }),
    });
    const assignData = await assignResp.json();
    if (!assignResp.ok) throw new Error(assignData.detail || "分配失败");
    logAgent(`任务 ${taskId} 已分配给 标注员1`);
  } catch (e) {
    alert("分配失败: " + e.message);
  }
});

// 15. start_label_item
jobList.addEventListener("dblclick", async (e) => {
  const item = e.target.closest(".list-item");
  if (!item) return;
  const jobId = item.dataset.id;
  try {
    const taskResp = await fetch(`${API}/api/jobs/${jobId}/tasks`);
    const taskData = await taskResp.json();
    const tasks = taskData.records || [];
    if (tasks.length === 0) return;
    const taskId = tasks[0].id;
    const labelResp = await fetch(`${API}/api/label/tasks/${taskId}/items/item-001`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}),
    });
    const labelData = await labelResp.json();
    if (!labelResp.ok) throw new Error(labelData.detail || "开始标注失败");
    logAgent(`开始标注: 任务 ${taskId}, 条目 item-001 → ${labelData.status}`);
  } catch (e) {
    logAgent(`开始标注失败: ${e.message}`);
  }
});

// 17. start_task (Agent)
$("agentStartTaskBtn").addEventListener("click", async () => {
  if (!lastTaskId) return alert("暂无任务");
  try {
    const resp = await fetch(`${API}/api/agent/start-task`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ taskId: lastTaskId }),
    });
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.detail || "启动失败");
    logAgent(`Agent 启动任务 ${lastTaskId}: ${data.status}`);
  } catch (e) {
    logAgent(`Agent 启动失败: ${e.message}`);
  }
});

// 20. report_progress
$("agentReportBtn").addEventListener("click", async () => {
  if (!lastTaskId) return alert("暂无任务");
  try {
    const resp = await fetch(`${API}/api/agent/report-progress`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ taskId: lastTaskId, progress: 50, message: "标注进行中", label: "气孔", confidence: 0.95 }),
    });
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.detail || "汇报失败");
    logAgent(`进度汇报: ${data.progress}%, 标签=${data.label}, 置信度=${data.confidence}`);
  } catch (e) {
    logAgent(`进度汇报失败: ${e.message}`);
  }
});

// 18. request_human
$("agentRequestHumanBtn").addEventListener("click", async () => {
  if (!lastTaskId) return alert("暂无任务");
  try {
    const resp = await fetch(`${API}/api/agent/request-human`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ taskId: lastTaskId, imageId: "img-001", message: "不确定，请人工审核", preLabel: "气孔", preConfidence: 0.6 }),
    });
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.detail || "请求失败");
    logAgent(`请求人工审核: ${data.id}, 状态=${data.status}`);
  } catch (e) {
    logAgent(`请求人工审核失败: ${e.message}`);
  }
});

// 19. confirm_human
$("agentConfirmHumanBtn").addEventListener("click", async () => {
  if (!lastTaskId) return alert("暂无任务");
  try {
    const resp = await fetch(`${API}/api/agent/confirm-human`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ taskId: lastTaskId, imageId: "img-001", humanLabel: "裂纹", message: "确认" }),
    });
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.detail || "确认失败");
    logAgent(`人工审核确认: ${data.status}`);
  } catch (e) {
    logAgent(`人工审核确认失败: ${e.message}`);
  }
});

// LLM 汇报关闭
llmReportClose.addEventListener("click", () => { llmReport.hidden = true; });

// ===== MCP 调用日志（NDJSON streamable） =====

const mcpDrawer = $("mcpDrawer");
const mcpDrawerHeader = $("mcpDrawerHeader");
const mcpDrawerToggle = $("mcpDrawerToggle");
const mcpDrawerBody = $("mcpDrawerBody");
const mcpLogList = $("mcpLogList");
const mcpLogCount = $("mcpLogCount");
const mcpClearBtn = $("mcpClearBtn");
const mcpStreamStatus = $("mcpStreamStatus");

let mcpLogTotal = 0;
let mcpDrawerExpanded = false;
// seq → pending 条目，等待结果回填
const mcpPending = {};

function setMcpStreamStatus(state, text) {
  const dot = mcpStreamStatus.querySelector(".dot");
  const label = mcpStreamStatus.querySelector(".mcp-status-text");
  dot.className = `dot dot-${state}`;
  label.textContent = text;
}

function formatArgs(args) {
  if (!args || typeof args !== "object") return "";
  try {
    const s = JSON.stringify(args);
    return s.length > 300 ? s.slice(0, 300) + "…" : s;
  } catch {
    return String(args);
  }
}

function appendMcpLog(entry) {
  // 心跳空行：忽略
  if (!entry || Object.keys(entry).length === 0) return;

  // 连接成功
  if (entry.type === "connected") {
    setMcpStreamStatus("ok", "已连接");
    return;
  }

  mcpLogTotal++;
  mcpLogCount.hidden = false;
  mcpLogCount.textContent = mcpLogTotal;

  // 移除空提示
  const empty = mcpLogList.querySelector(".mcp-log-empty");
  if (empty) empty.remove();

  if (entry.type === "mcp_call") {
    // pending 条目：插入一条，记录 DOM 引用
    const el = renderMcpEntry(entry);
    mcpLogList.appendChild(el);
    mcpPending[entry.seq] = el;
  } else if (entry.type === "mcp_result") {
    // 用 pending 的 seq 找到对应条目回填；找不到则新建
    // 后端 result 没带 seq，用 tool + 时间戳近似匹配最后一条 pending
    const pendingSeq = findPendingSeq(entry.tool);
    if (pendingSeq && mcpPending[pendingSeq]) {
      updateMcpEntry(mcpPending[pendingSeq], entry);
      delete mcpPending[pendingSeq];
    } else {
      mcpLogList.appendChild(renderMcpEntry(entry));
    }
  }

  // 自动滚动到底部
  mcpLogList.scrollTop = mcpLogList.scrollHeight;

  // 有新日志时自动展开抽屉
  if (!mcpDrawerExpanded) toggleDrawer(true);
}

function findPendingSeq(tool) {
  // 找最后一条同 tool 的 pending
  const seqs = Object.keys(mcpPending).map(Number).sort((a, b) => a - b);
  for (let i = seqs.length - 1; i >= 0; i--) {
    const el = mcpPending[seqs[i]];
    if (el && el.dataset.tool === tool) return seqs[i];
  }
  return null;
}

function renderMcpEntry(entry) {
  const el = document.createElement("div");
  el.className = "mcp-log-entry";
  el.dataset.tool = entry.tool || "";

  const status = entry.status || "pending";
  el.dataset.status = status;

  const ts = entry.timestamp || new Date().toLocaleTimeString();
  const tool = entry.tool || "(unknown)";

  let html = `<div class="mcp-entry-row mcp-entry-head">`;
  html += `<span class="mcp-entry-status mcp-status-${status}"></span>`;
  html += `<span class="mcp-entry-tool">${escapeHtml(tool)}</span>`;
  html += `<span class="mcp-entry-ts">${escapeHtml(ts)}</span>`;
  if (entry.elapsed_ms != null) {
    html += `<span class="mcp-entry-elapsed">${entry.elapsed_ms}ms</span>`;
  }
  html += `</div>`;

  if (entry.args) {
    html += `<div class="mcp-entry-row"><span class="mcp-entry-label">参数</span><code class="mcp-entry-code">${escapeHtml(formatArgs(entry.args))}</code></div>`;
  }
  if (entry.result) {
    html += `<div class="mcp-entry-row"><span class="mcp-entry-label">返回</span><code class="mcp-entry-code mcp-code-result">${escapeHtml(entry.result)}</code></div>`;
  }
  if (entry.error) {
    html += `<div class="mcp-entry-row"><span class="mcp-entry-label">错误</span><code class="mcp-entry-code mcp-code-error">${escapeHtml(entry.error)}</code></div>`;
  }
  if (status === "pending") {
    html += `<div class="mcp-entry-row mcp-entry-pending"><span class="mcp-entry-spinner"></span>调用中…</div>`;
  }

  el.innerHTML = html;
  return el;
}

function updateMcpEntry(el, entry) {
  const status = entry.status || "success";
  el.dataset.status = status;

  // 更新状态点
  const dot = el.querySelector(".mcp-entry-status");
  if (dot) dot.className = `mcp-entry-status mcp-status-${status}`;

  // 加耗时
  if (entry.elapsed_ms != null) {
    const head = el.querySelector(".mcp-entry-head");
    if (head && !head.querySelector(".mcp-entry-elapsed")) {
      const span = document.createElement("span");
      span.className = "mcp-entry-elapsed";
      span.textContent = `${entry.elapsed_ms}ms`;
      head.appendChild(span);
    }
  }

  // 移除 pending 提示
  const pending = el.querySelector(".mcp-entry-pending");
  if (pending) pending.remove();

  // 追加结果/错误
  if (entry.result) {
    el.insertAdjacentHTML("beforeend",
      `<div class="mcp-entry-row"><span class="mcp-entry-label">返回</span><code class="mcp-entry-code mcp-code-result">${escapeHtml(entry.result)}</code></div>`);
  }
  if (entry.error) {
    el.insertAdjacentHTML("beforeend",
      `<div class="mcp-entry-row"><span class="mcp-entry-label">错误</span><code class="mcp-entry-code mcp-code-error">${escapeHtml(entry.error)}</code></div>`);
  }
}

function toggleDrawer(forceExpand) {
  const shouldExpand = forceExpand != null ? forceExpand : !mcpDrawerExpanded;
  mcpDrawerExpanded = shouldExpand;
  mcpDrawerBody.hidden = !shouldExpand;
  mcpDrawerToggle.textContent = shouldExpand ? "收起" : "展开";
  mcpDrawer.classList.toggle("mcp-drawer-expanded", shouldExpand);
}

mcpDrawerHeader.addEventListener("click", (e) => {
  if (e.target.closest(".mcp-drawer-actions")) return;
  toggleDrawer();
});

mcpClearBtn.addEventListener("click", (e) => {
  e.stopPropagation();
  mcpLogList.innerHTML = '<div class="mcp-log-empty">已清空</div>';
  mcpLogTotal = 0;
  mcpLogCount.hidden = true;
  for (const k in mcpPending) delete mcpPending[k];
});

// NDJSON 流式读取（fetch + ReadableStream）
async function connectMcpLogStream() {
  setMcpStreamStatus("idle", "连接中...");
  let buffer = "";

  async function consume() {
    const resp = await fetch(`${API}/api/mcp-log/stream`);
    if (!resp.ok || !resp.body) {
      setMcpStreamStatus("err", "连接失败");
      setTimeout(consume, 3000); // 重连
      return;
    }

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();

    try {
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        // 按行分割
        let idx;
        while ((idx = buffer.indexOf("\n")) >= 0) {
          const line = buffer.slice(0, idx).trim();
          buffer = buffer.slice(idx + 1);
          if (!line) continue;
          try {
            const entry = JSON.parse(line);
            appendMcpLog(entry);
          } catch {
            // 忽略无法解析的行
          }
        }
      }
    } catch (e) {
      // 网络中断等
    } finally {
      try { reader.releaseLock(); } catch {}
    }

    // 流结束，尝试重连
    setMcpStreamStatus("warn", "已断开，重连中...");
    setTimeout(consume, 3000);
  }

  try {
    await consume();
  } catch (e) {
    setMcpStreamStatus("err", "连接失败");
    setTimeout(connectMcpLogStream, 3000);
  }
}

// ===== 初始化 =====
loadDatasets();
connectMcpLogStream();
