(() => {
  "use strict";
  const boot = JSON.parse(document.getElementById("fs09-phase-truth-bootstrap").textContent);
  const $ = (id) => document.getElementById(id);
  const video = $("video");
  const phaseSpecs = [["peak_speed", "峰速时刻"], ["deceleration_peak", "减速峰时刻"], ["restabilization_onset", "重新稳定开始"], ["stable_control_onset", "稳定控制开始"]];
  const comparisonFields = [
    ["event_present", "事件存在"], ["event_start_ms", "事件起点"], ["event_end_ms", "事件终点"], ["event_reason", "事件原因"],
    ["peak_speed_status", "峰速时刻状态"], ["peak_speed_ms", "峰速时刻时间"], ["peak_speed_reason", "峰速时刻原因"],
    ["deceleration_peak_status", "减速峰时刻状态"], ["deceleration_peak_ms", "减速峰时刻时间"], ["deceleration_peak_reason", "减速峰时刻原因"],
    ["restabilization_onset_status", "重新稳定开始状态"], ["restabilization_onset_ms", "重新稳定开始时间"], ["restabilization_onset_reason", "重新稳定开始原因"],
    ["stable_control_onset_status", "稳定控制开始状态"], ["stable_control_onset_ms", "稳定控制开始时间"], ["stable_control_onset_reason", "稳定控制开始原因"],
    ["confidence", "信心"], ["notes", "备注"],
  ];
  const imported = new Map();
  const handoffBundleId = typeof boot.handoff_bundle_id === "string" ? boot.handoff_bundle_id : "";
  const analysisPlanSha256 = typeof boot.analysis_plan_sha256 === "string" ? boot.analysis_plan_sha256 : "";
  const handoffIdentityReady = /^m88-fs09-blind-[A-F0-9]{64}$/.test(handoffBundleId) && /^[0-9A-F]{64}$/.test(analysisPlanSha256);
  const annotationExecutionAuthorized = boot.annotation_execution_authorized === true && boot.external_protocol_receipt_verified === true;
  const handoffReady = handoffIdentityReady && annotationExecutionAuthorized;
  const handoffGateMessage = handoffIdentityReady
    ? "当前 blind handoff 仅技术验证通过；尚无已验证的独立外部协议回执与可信锚，禁止首次作答、保存、导入和导出。"
    : "工作台来源门禁未通过：必须使用 public blind handoff（缺少或无效的 handoff_bundle_id / analysis_plan_sha256），已禁止保存、导入和导出。";
  const storageKey = `rallymate-m77-fs09-phase-truth-v3:${handoffBundleId}:${analysisPlanSha256}`;
  const invalidIdCharacters = /[;\r\n\u2028\u2029]/;
  let drafts = {}; let dirty = false; let activeContext = null; let mediaReady = false; let lastExportedAt = "";
  if (handoffReady) { try { drafts = JSON.parse(localStorage.getItem(storageKey) || "{}"); } catch (_) { drafts = {}; } }

  function csvEscape(value) { const s = value == null ? "" : String(value); return /[",\r\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s; }
  function parseCsv(text) {
    const rows = []; let row = []; let cell = ""; let quoted = false;
    for (let i = 0; i < text.length; i += 1) {
      const ch = text[i];
      if (quoted) { if (ch === '"' && text[i + 1] === '"') { cell += '"'; i += 1; } else if (ch === '"') quoted = false; else cell += ch; }
      else if (ch === '"') quoted = true;
      else if (ch === ",") { row.push(cell); cell = ""; }
      else if (ch === "\n") { row.push(cell.replace(/\r$/, "")); rows.push(row); row = []; cell = ""; }
      else cell += ch;
    }
    if (quoted) throw new Error("CSV 含有未闭合的引号");
    if (cell || row.length) { row.push(cell.replace(/\r$/, "")); rows.push(row); }
    if (!rows.length) throw new Error("CSV 为空");
    const headers = rows[0].map((value, index) => index === 0 ? value.replace(/^\uFEFF/, "") : value);
    const values = rows.slice(1).filter((cells) => cells.some(Boolean));
    if (values.some((cells) => cells.length !== headers.length)) throw new Error("CSV 数据列数与 header 不一致");
    return {headers, rows: values.map((cells) => Object.fromEntries(headers.map((name, index) => [name, cells[index]])))};
  }
  function requireAnnotationHeaders(headers) {
    const expected = boot.annotation_fields;
    if (headers.length !== expected.length || headers.some((name, index) => name !== expected[index])) throw new Error(`annotation CSV header 必须严格等于：${expected.join(",")}`);
  }
  function download(name, headers, rows) {
    const csv = [headers.join(","), ...rows.map((row) => headers.map((key) => csvEscape(row[key])).join(","))].join("\r\n");
    const link = document.createElement("a"); link.href = URL.createObjectURL(new Blob(["\uFEFF", csv], {type: "text/csv;charset=utf-8"})); link.download = name; link.click(); URL.revokeObjectURL(link.href);
  }
  const nowIso = () => new Date().toISOString();
  async function sha256Hex(text) { const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text)); return [...new Uint8Array(digest)].map((value) => value.toString(16).padStart(2, "0")).join("").toUpperCase(); }
  function annotationRevisionPayload(row) { const keys = boot.annotation_fields.filter((name) => !["annotation_revision_sha256", "exported_at"].includes(name)).sort(); return JSON.stringify(Object.fromEntries(keys.map((name) => [name, row[name] == null ? "" : String(row[name])]))); }
  function nextIso(previous) {
    const current = nowIso();
    if (!previous) return current;
    const currentMs = Date.parse(current); const previousMs = Date.parse(previous);
    if (!Number.isFinite(previousMs) || (Number.isFinite(currentMs) && currentMs > previousMs)) return current;
    return new Date(previousMs + 1).toISOString();
  }
  function nextExportedAt() { lastExportedAt = nextIso(lastExportedAt); return lastExportedAt; }
  const identityKey = (value) => String(value || "").trim().normalize("NFKC").toLowerCase();
  const taskById = (taskId) => boot.tasks.find((row) => row.task_id === taskId);
  const task = () => taskById($("task").value);
  const phaseId = (prefix, suffix) => `${prefix}-${suffix}`;
  const contextFromControls = () => ({mode: $("mode").value, identity: $("identity").value.trim(), taskId: $("task").value});
  const contextKey = (context) => `${context.mode}:${context.identity}:${context.taskId}`;
  const currentMs = () => String(task().source_time_offset_ms + Math.round(video.currentTime * 1000));

  phaseSpecs.forEach(([prefix, label]) => {
    const row = document.createElement("div"); row.className = "phase";
    row.innerHTML = `<h3>${label}</h3><label>状态<select id="${phaseId(prefix,"status")}"><option value="">请选择（必填）</option><option value="observed">可观察并标时</option><option value="not_observed">事件内未出现</option><option value="unobservable">画面不可判断</option></select></label><label>timestamp_ms<input id="${phaseId(prefix,"ms")}" inputmode="numeric"></label><button type="button" data-capture="${phaseId(prefix,"ms")}">取当前视频时间</button><label>未出现/不可判断原因<input id="${phaseId(prefix,"reason")}"></label>`;
    $("phases").appendChild(row);
  });
  boot.tasks.forEach((row, index) => { const option = document.createElement("option"); option.value = row.task_id; option.textContent = `任务 ${index + 1} · FS09-M05`; $("task").appendChild(option); });

  const timeControls = document.createElement("div"); timeControls.id = "time-controls"; timeControls.className = "time-controls";
  timeControls.innerHTML = `<button type="button" data-step-frame="-1">−1 frame</button><button type="button" data-step-ms="-100">−100 ms</button><output id="video-time" aria-live="polite">local — · source —</output><button type="button" data-step-ms="100">+100 ms</button><button type="button" data-step-frame="1">+1 frame</button><span id="video-fps"></span>`;
  video.insertAdjacentElement("afterend", timeControls);
  const comparison = document.createElement("section"); comparison.id = "reviewer-comparison"; comparison.className = "reviewer-comparison"; comparison.hidden = true;
  comparison.innerHTML = `<h2>独立标注对照（仅人工输入）</h2><p id="comparison-status"></p><div id="comparison-grid"></div>`; $("editor").insertAdjacentElement("afterend", comparison);

  function emptyRecord() {
    const record = {event_present: "", event_start_ms: "", event_end_ms: "", event_reason: "", confidence: "", notes: ""};
    phaseSpecs.forEach(([prefix]) => { record[`${prefix}_status`] = ""; record[`${prefix}_ms`] = ""; record[`${prefix}_reason`] = ""; }); return record;
  }
  const decisionFieldNames = Object.keys(emptyRecord());
  function decisionOnly(record) { return Object.fromEntries(decisionFieldNames.map((name) => [name, record && record[name] != null ? record[name] : ""])); }
  function decisionsEqual(left, right) { return decisionFieldNames.every((name) => (left && left[name] != null ? left[name] : "") === (right && right[name] != null ? right[name] : "")); }
  const decisionTimestampField = (mode) => mode === "adjudicate" ? "adjudicated_at" : "annotated_at";
  function requireHandoff() {
    if (handoffReady) return true;
    const status = $("status"); if (status) { status.textContent = handoffGateMessage; status.className = "error"; }
    return false;
  }
  function readForm() {
    const record = emptyRecord(); record.event_present = $("event-present").value;
    record.event_start_ms = $("event-start").value.trim(); record.event_end_ms = $("event-end").value.trim(); record.event_reason = $("event-reason").value.trim(); record.confidence = $("confidence").value.trim(); record.notes = $("notes").value.trim();
    phaseSpecs.forEach(([prefix]) => { record[`${prefix}_status`] = $(phaseId(prefix,"status")).value; record[`${prefix}_ms`] = $(phaseId(prefix,"ms")).value.trim(); record[`${prefix}_reason`] = $(phaseId(prefix,"reason")).value.trim(); }); return record;
  }
  function writeForm(record) {
    const value = {...emptyRecord(), ...(record || {})}; $("event-present").value = value.event_present === true ? "true" : value.event_present === false ? "false" : value.event_present || "";
    $("event-start").value = value.event_start_ms || ""; $("event-end").value = value.event_end_ms || ""; $("event-reason").value = value.event_reason || ""; $("confidence").value = value.confidence || ""; $("notes").value = value.notes || "";
    phaseSpecs.forEach(([prefix]) => { $(phaseId(prefix,"status")).value = value[`${prefix}_status`] || ""; $(phaseId(prefix,"ms")).value = value[`${prefix}_ms`] || ""; $(phaseId(prefix,"reason")).value = value[`${prefix}_reason`] || ""; }); dirty = false;
  }
  function validateRecord(record, currentTask, identity) {
    if (!identity) return "必须填写唯一 annotator/reviewer ID";
    if (invalidIdCharacters.test(identity)) return "annotator/reviewer ID 不得包含分号或换行";
    if (!["true", "false"].includes(record.event_present)) return "必须主动选择事件存在或不存在/不可观测";
    const present = record.event_present === "true"; const start = Number(record.event_start_ms); const end = Number(record.event_end_ms);
    if (present && (!/^\d+$/.test(record.event_start_ms) || !/^\d+$/.test(record.event_end_ms) || start >= end)) return "存在事件时必须填写有序 start/end";
    if (present && (start < currentTask.review_start_ms || end > currentTask.review_end_ms)) return "事件边界必须位于当前盲化审阅窗口内";
    if (!present && (record.event_start_ms || record.event_end_ms || !record.event_reason)) return "事件不存在时边界须留空并填写原因";
    const observed = [];
    for (const [prefix] of phaseSpecs) {
      const status = record[`${prefix}_status`]; const ms = record[`${prefix}_ms`]; const reason = record[`${prefix}_reason`];
      if (!["observed", "not_observed", "unobservable"].includes(status)) return `${prefix} 必须主动选择阶段状态`;
      if (status === "observed" && (!present || !/^\d+$/.test(ms))) return `${prefix} 可观察时必须填写时间`;
      if (status === "observed" && (Number(ms) < start || Number(ms) > end)) return `${prefix} 必须位于事件边界内`;
      if (status === "observed") observed.push(Number(ms));
      if (status !== "observed" && (ms || !reason)) return `${prefix} 未出现/不可判断时必须留空时间并填写原因`;
    }
    if (observed.some((value, index) => index > 0 && value < observed[index - 1])) return "可观察 FS09 阶段时间必须单调不减";
    if (record.confidence === "") return "必须主动填写 confidence";
    const confidence = Number(record.confidence); if (!Number.isFinite(confidence) || confidence < 0 || confidence > 1) return "confidence 必须在 0–1"; return "";
  }
  function saveContext(context, record = readForm(), announce = true) {
    if (!requireHandoff()) return false;
    const error = validateRecord(record, taskById(context.taskId), context.identity);
    if (error) { $("status").textContent = error; $("status").className = "error"; return false; }
    const key = contextKey(context); const previous = drafts[key]; const decision = decisionOnly(record); const timestampField = decisionTimestampField(context.mode);
    const sourceRevisionBinding = context.mode === "adjudicate" ? sourceRevisionBindingForTask(context.taskId) : "";
    if (context.mode === "adjudicate" && importedForTask(context.taskId).length !== boot.required_annotators) { $("status").textContent = "裁决保存前必须导入当前 bundle 下两名标注者的精确 annotation revisions"; $("status").className = "error"; return false; }
    const changed = !previous || !decisionsEqual(previous, decision) || (context.mode === "adjudicate" && previous._source_annotation_revision_binding !== sourceRevisionBinding); const decisionAt = changed || !previous[timestampField] ? nextIso(previous && previous[timestampField]) : previous[timestampField];
    drafts[key] = {...decision, [timestampField]: decisionAt, ...(context.mode === "adjudicate" ? {_source_annotation_revision_binding: sourceRevisionBinding} : {})}; localStorage.setItem(storageKey, JSON.stringify(drafts)); dirty = false;
    if (announce) { $("status").textContent = "已保存浏览器草稿；仍需导出 CSV 并运行 Python 编译"; $("status").className = "ok"; } updateProgress(); return true;
  }
  function save() { const controls = contextFromControls(); if (!activeContext || contextKey(controls) !== contextKey(activeContext)) activeContext = controls; return saveContext(activeContext); }
  function taskFps() { const value = boot.review_clip_fps && boot.review_clip_fps[task().task_id]; const fps = Number(value && typeof value === "object" ? value.fps : value); return Number.isFinite(fps) && fps > 0 ? fps : null; }
  function applyMediaGate() {
    document.querySelectorAll("[data-capture], [data-step-ms], #seek-start, #seek-anchor").forEach((button) => { button.disabled = !mediaReady; });
    document.querySelectorAll("#export-annotations, #export-adjudications").forEach((button) => { button.disabled = !mediaReady || !handoffReady; });
    const fps = taskFps(); document.querySelectorAll("[data-step-frame]").forEach((button) => { button.disabled = !mediaReady || !fps; });
    $("save").disabled = !handoffReady; $("import").disabled = !handoffReady;
  }
  function updateMediaReadiness() {
    const duration = Number(video.duration); const fps = taskFps(); const tolerance = Math.max(0.05, fps ? (1 / fps) + 0.01 : 0.06); let fullRange = false;
    if (Number.isFinite(duration) && duration > 0 && video.seekable.length) {
      for (let index = 0; index < video.seekable.length; index += 1) {
        if (video.seekable.start(index) <= tolerance && video.seekable.end(index) >= duration - tolerance) { fullRange = true; break; }
      }
    }
    mediaReady = !video.error && Number.isFinite(duration) && duration > 0 && fullRange;
    const status = $("media-status");
    if (video.error) { status.textContent = "媒体门禁未通过：短片加载失败。取时、步进和导出已禁用。"; status.className = "error"; }
    else if (mediaReady) { status.textContent = `媒体门禁通过：可逐帧定位完整 ${duration.toFixed(3)} 秒短片。`; status.className = "ok"; }
    else if (Number.isFinite(duration) && duration > 0) { status.textContent = "媒体门禁未通过：服务器没有提供覆盖完整短片的 HTTP byte range。请按 public blind handoff 内的 OPERATOR_README.md 启动随包提供的 allowlist Range server；取时、步进和导出已禁用。"; status.className = "error"; }
    else { status.textContent = "正在验证媒体是否支持完整逐帧定位……"; status.className = ""; }
    applyMediaGate();
  }
  function updateVideoTime() {
    const currentTask = task(); if (!currentTask) return; const local = Math.round(video.currentTime * 1000); $("video-time").textContent = `local ${local} ms · source ${currentTask.source_time_offset_ms + local} ms`;
    const fps = taskFps(); $("video-fps").textContent = fps ? `${fps} fps${mediaReady ? "" : " · 媒体门禁未就绪"}` : "FPS 未绑定，frame 步进停用"; applyMediaGate();
  }
  function stepVideo(seconds) { if (!mediaReady) return; const duration = Number.isFinite(video.duration) ? video.duration : Math.max(0, (task().review_end_ms - task().review_start_ms) / 1000); video.currentTime = Math.min(duration, Math.max(0, video.currentTime + seconds)); updateVideoTime(); }
  function importedForTask(taskId) { return [...imported.values()].filter((row) => row.task_id === taskId).sort((a, b) => a.annotator_id.localeCompare(b.annotator_id) || a.annotation_id.localeCompare(b.annotation_id)); }
  function importedAnnotators() { return new Set([...imported.values()].map((row) => identityKey(row.annotator_id))); }
  function sourceRevisionBindingForTask(taskId) { return importedForTask(taskId).map((row) => `${row.annotation_id}=${row.annotation_revision_sha256}`).sort().join(";"); }

  function renderComparison() {
    const adjudicating = $("mode").value === "adjudicate"; comparison.hidden = !adjudicating; if (!adjudicating) return;
    const rows = importedForTask($("task").value); const annotators = new Set(rows.map((row) => identityKey(row.annotator_id))); const allAnnotators = importedAnnotators(); const reviewer = identityKey($("identity").value); const status = $("comparison-status"); const grid = $("comparison-grid"); grid.replaceChildren();
    if (allAnnotators.size !== boot.required_annotators || annotators.size !== boot.required_annotators) { status.textContent = `整个试点及当前任务都必须精确包含同一组 ${boot.required_annotators} 名独立标注者；当前全局/任务人数为 ${allAnnotators.size}/${annotators.size}。请只导入 A/B 两份完整 annotation CSV。`; status.className = "error"; return; }
    if (reviewer && annotators.has(reviewer)) { status.textContent = "reviewer ID 与当前任务的 annotator ID 重复，不能独立裁决。"; status.className = "error"; }
    else { status.textContent = `已载入 ${annotators.size} 名独立标注者；候选事件、候选阶段和 Pose 均未加载。`; status.className = "ok"; }
    const selected = rows;
    const table = document.createElement("div"); table.className = "comparison-table"; const header = document.createElement("div"); header.className = "comparison-row comparison-header";
    ["字段", ...selected.map((row) => `${row.annotator_id} · ${row.annotation_id}`), "差异"].forEach((value) => { const cell = document.createElement("div"); cell.textContent = value; header.appendChild(cell); }); table.appendChild(header);
    comparisonFields.forEach(([field, label]) => { const values = selected.map((row) => row[field] || "—"); const differs = new Set(values).size > 1; const line = document.createElement("div"); line.className = `comparison-row${differs ? " differs" : ""}`; [label, ...values, differs ? "不同" : "一致"].forEach((value) => { const cell = document.createElement("div"); cell.textContent = value; line.appendChild(cell); }); table.appendChild(line); }); grid.appendChild(table);
  }
  function updateProgress() {
    const context = activeContext || contextFromControls(); const prefix = `${context.mode}:${context.identity}:`; const count = boot.tasks.filter((item) => drafts[prefix + item.task_id]).length; const perTask = boot.tasks.map((item) => new Set(importedForTask(item.task_id).map((row) => row.annotator_id)).size);
    $("progress").textContent = `当前身份已保存 ${count}/${boot.tasks.length}；已导入 ${imported.size} 条人工标注（各任务独立标注者：${perTask.join("/")}）。`;
  }
  function loadContext(context) {
    activeContext = {...context}; $("mode").value = context.mode; $("identity").value = context.identity; $("task").value = context.taskId; const currentTask = task(); if (!currentTask) return;
    $("window").textContent = `盲化审阅窗口：原视频 ${currentTask.review_start_ms}–${currentTask.review_end_ms} ms；目标动作粗定位锚点 ${currentTask.target_selection_anchor_ms} ms。请用它区分窗口内动作，但独立判断事件边界与阶段；锚点不是边界/阶段，也不限制你提交的 start/end。播放器使用无叠加短片，记录值自动换算为原视频绝对 timestamp_ms。`;
    if (video.dataset.taskId !== currentTask.task_id) { mediaReady = false; $("media-status").textContent = "正在验证媒体是否支持完整逐帧定位……"; $("media-status").className = ""; applyMediaGate(); video.dataset.taskId = currentTask.task_id; video.src = boot.review_clips[currentTask.task_id]; video.load(); } else { video.currentTime = 0; updateMediaReadiness(); }
    writeForm(drafts[contextKey(context)] || null); updateProgress(); updateVideoTime(); renderComparison();
  }
  function transition(nextContext) {
    const firstIdentity = activeContext && !activeContext.identity && nextContext.identity && activeContext.mode === nextContext.mode && activeContext.taskId === nextContext.taskId;
    const saveTarget = firstIdentity ? nextContext : activeContext;
    if (activeContext && dirty && !saveContext(saveTarget, readForm(), false)) { $("mode").value = activeContext.mode; $("identity").value = activeContext.identity; $("task").value = activeContext.taskId; alert("当前表单无效，已阻止切换以避免静默丢稿。请修正或保存后再切换。"); return false; }
    loadContext(nextContext); return true;
  }
  function rowsForMode(mode) { const identity = $("identity").value.trim(); return boot.tasks.map((item) => ({task: item, value: drafts[`${mode}:${identity}:${item.task_id}`]})).filter((item) => item.value); }
  async function annotationRows() { const identity = $("identity").value.trim(); const exportedAt = nextExportedAt(); const rows = rowsForMode("annotate").map(({task: item, value}) => ({handoff_bundle_id: handoffBundleId, analysis_plan_sha256: analysisPlanSha256, annotation_id: `${identity}:${item.task_id}`, task_id: item.task_id, annotator_id: identity, ...decisionOnly(value), annotated_at: value.annotated_at, exported_at: exportedAt})); return Promise.all(rows.map(async (row) => ({...row, annotation_revision_sha256: await sha256Hex(annotationRevisionPayload(row))}))); }
  function adjudicationRows() { const identity = $("identity").value.trim(); const exportedAt = nextExportedAt(); return rowsForMode("adjudicate").map(({task: item, value}) => { const sources = importedForTask(item.task_id).filter((row) => row.annotation_id).sort((a, b) => a.annotation_id.localeCompare(b.annotation_id)); return {handoff_bundle_id: handoffBundleId, analysis_plan_sha256: analysisPlanSha256, adjudication_id: `${identity}:${item.task_id}`, task_id: item.task_id, source_annotation_ids: sources.map((row) => row.annotation_id).join(";"), source_annotation_revision_sha256s: sources.map((row) => row.annotation_revision_sha256).join(";"), reviewer_id: identity, ...decisionOnly(value), adjudicated_at: value.adjudicated_at, exported_at: exportedAt, status: "accepted"}; }); }
  async function validateImportedRows(candidateRows) {
    const taskIds = new Set(boot.tasks.map((item) => item.task_id)); const combined = new Map(imported);
    for (const row of candidateRows) { if (row.handoff_bundle_id !== handoffBundleId || row.analysis_plan_sha256 !== analysisPlanSha256) throw new Error("annotation CSV 与当前 public blind handoff bundle/analysis plan 不匹配"); if (!row.annotation_id || !row.task_id || !row.annotator_id || !row.annotated_at || !row.exported_at || !/^[A-F0-9]{64}$/.test(row.annotation_revision_sha256 || "")) throw new Error("annotation_id/task_id/annotator_id/timestamps/revision SHA 均为必填"); if (await sha256Hex(annotationRevisionPayload(row)) !== row.annotation_revision_sha256) throw new Error(`annotation revision digest 不匹配：${row.annotation_id}`); if (invalidIdCharacters.test(row.annotation_id)) throw new Error(`annotation_id 不得包含分号或换行：${row.annotation_id}`); if (!taskIds.has(row.task_id)) throw new Error(`未知 task_id：${row.task_id}`); const error = validateRecord(row, taskById(row.task_id), row.annotator_id); if (error) throw new Error(`${row.annotation_id}：${error}`); const existing = combined.get(row.annotation_id); if (existing && (existing.task_id !== row.task_id || existing.annotator_id !== row.annotator_id || existing.annotation_revision_sha256 !== row.annotation_revision_sha256)) throw new Error(`annotation_id revision 冲突：${row.annotation_id}`); combined.set(row.annotation_id, row); }
    const seen = new Set(); combined.forEach((row) => { const pair = `${row.task_id}\u0000${identityKey(row.annotator_id)}`; if (seen.has(pair)) throw new Error(`同一 annotator 每个任务只能提交一次：${row.annotator_id} / ${row.task_id}`); seen.add(pair); });
    const allAnnotators = new Set([...combined.values()].map((row) => identityKey(row.annotator_id))); if (allAnnotators.size > boot.required_annotators) throw new Error(`整个试点只能导入精确 ${boot.required_annotators} 名 annotator`);
    boot.tasks.forEach((item) => { const annotators = new Set([...combined.values()].filter((row) => row.task_id === item.task_id).map((row) => identityKey(row.annotator_id))); if (annotators.size > boot.required_annotators) throw new Error(`任务 ${item.task_id} 只能导入精确 ${boot.required_annotators} 名 annotator`); }); return combined;
  }

  document.querySelectorAll("#editor input, #editor select, #editor textarea").forEach((control) => { control.addEventListener("input", () => { dirty = true; }); control.addEventListener("change", () => { dirty = true; }); });
  document.querySelectorAll("[data-capture]").forEach((button) => button.addEventListener("click", () => { if (!mediaReady) return; $(button.dataset.capture).value = currentMs(); dirty = true; }));
  document.querySelectorAll("[data-step-ms]").forEach((button) => button.addEventListener("click", () => stepVideo(Number(button.dataset.stepMs) / 1000)));
  document.querySelectorAll("[data-step-frame]").forEach((button) => button.addEventListener("click", () => { const fps = taskFps(); if (fps) stepVideo(Number(button.dataset.stepFrame) / fps); }));
  ["loadedmetadata", "durationchange", "loadeddata", "progress", "canplay", "canplaythrough"].forEach((name) => video.addEventListener(name, updateMediaReadiness)); ["loadedmetadata", "timeupdate", "seeked"].forEach((name) => video.addEventListener(name, updateVideoTime)); video.addEventListener("loadedmetadata", () => { video.currentTime = 0; updateMediaReadiness(); }); video.addEventListener("error", updateMediaReadiness);
  $("seek-start").addEventListener("click", () => { if (!mediaReady) return; video.currentTime = 0; updateVideoTime(); });
  $("seek-anchor").addEventListener("click", () => { if (!mediaReady) return; video.currentTime = Math.max(0, (task().target_selection_anchor_ms - task().source_time_offset_ms) / 1000); updateVideoTime(); }); $("save").addEventListener("click", save);
  $("task").addEventListener("change", () => transition(contextFromControls())); $("mode").addEventListener("change", () => transition(contextFromControls())); $("identity").addEventListener("change", () => transition(contextFromControls()));
  $("previous").addEventListener("click", () => { const index = Math.max(0, $("task").selectedIndex - 1); transition({...contextFromControls(), taskId: boot.tasks[index].task_id}); });
  $("next").addEventListener("click", () => { const index = Math.min(boot.tasks.length - 1, $("task").selectedIndex + 1); transition({...contextFromControls(), taskId: boot.tasks[index].task_id}); });
  $("export-annotations").addEventListener("click", async () => { if (!requireHandoff()) return alert(handoffGateMessage); if (!mediaReady) return alert("媒体门禁未通过，禁止导出"); if ($("mode").value !== "annotate") return alert("请切换到独立标注模式"); if (!save()) return; const rows = await annotationRows(); if (rows.length !== boot.tasks.length) return alert("必须完成并成功保存全部任务后导出"); download(`m77-annotations-${$("identity").value.trim()}.csv`, boot.annotation_fields, rows); });
  $("export-adjudications").addEventListener("click", () => {
    if (!requireHandoff()) return alert(handoffGateMessage); if (!mediaReady) return alert("媒体门禁未通过，禁止导出"); if ($("mode").value !== "adjudicate") return alert("请切换到独立裁决模式"); if (!save()) return; const rows = adjudicationRows(); const reviewer = $("identity").value.trim();
    const allAnnotators = importedAnnotators(); const invalidSources = allAnnotators.size !== boot.required_annotators || allAnnotators.has(identityKey(reviewer)) || boot.tasks.some((item) => { const sourceRows = importedForTask(item.task_id); const annotators = new Set(sourceRows.map((row) => identityKey(row.annotator_id))); const draft = drafts[`adjudicate:${reviewer}:${item.task_id}`]; return annotators.size !== boot.required_annotators || !draft || draft._source_annotation_revision_binding !== sourceRevisionBindingForTask(item.task_id); });
    if (rows.length !== boot.tasks.length || invalidSources) return alert("每个任务必须精确导入两名不同 annotator，reviewer 必须独立，且每个裁决草稿必须绑定当前导入的精确 annotation revisions；来源变化后需逐任务重新核对并保存"); download(`m77-adjudications-${reviewer}.csv`, boot.adjudication_fields, rows);
  });
  $("import").addEventListener("change", async (event) => {
    try { if (!requireHandoff()) throw new Error(handoffGateMessage); const pending = []; for (const file of event.target.files) { const parsed = parseCsv(await file.text()); requireAnnotationHeaders(parsed.headers); pending.push(...parsed.rows); } if (!pending.length) throw new Error("annotation CSV 不含数据行"); const combined = await validateImportedRows(pending); imported.clear(); combined.forEach((row, id) => imported.set(id, row)); $("status").textContent = `成功导入 ${pending.length} 条人工标注及其精确 decision revisions；请逐任务核对差异。`; $("status").className = "ok"; }
    catch (error) { $("status").textContent = `导入失败：${error.message}`; $("status").className = "error"; alert($("status").textContent); }
    finally { event.target.value = ""; updateProgress(); renderComparison(); }
  });
  $("reset").addEventListener("click", () => { if (confirm("确认清空 M77 浏览器草稿？")) { drafts = {}; dirty = false; localStorage.removeItem(storageKey); writeForm(null); updateProgress(); } });
  window.addEventListener("beforeunload", (event) => { if (dirty) { event.preventDefault(); event.returnValue = ""; } });
  $("identity").value = ""; loadContext(contextFromControls()); requireHandoff();
})();
