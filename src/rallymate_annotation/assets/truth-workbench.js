(function () {
  "use strict";

  const bootNode = document.getElementById("truth-workbench-bootstrap");
  if (!bootNode) throw new Error("truth workbench bootstrap is missing");
  const BOOT = JSON.parse(bootNode.textContent);
  const WORKBENCH_VERSION = BOOT.workbench_version;
  const STORAGE_KEY = `rallymate-truth-workbench:${BOOT.manifest.pack_version}`;
  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));
  const clone = (value) => JSON.parse(JSON.stringify(value));
  const blank = (value) => value === undefined || value === null || String(value).trim() === "";
  const nowMs = () => Math.round(video.currentTime * 1000);
  const phaseLabel = (phase) => phase.replace(/_ms$/, "");

  const embeddedState = {
    state_version: WORKBENCH_VERSION,
    events: clone(BOOT.rows.events),
    keypoints: clone(BOOT.rows.keypoints),
    semantics: clone(BOOT.rows.semantics),
    reviews: clone(BOOT.rows.reviews),
    identity: { annotator_id: "", reviewer_id: "", view_group: "fixed-camera" },
    active_video_id: BOOT.manifest.videos[0]?.video_id || "",
    selected_event_key: "",
    selected_keypoint_key: "",
    selected_semantic_id: "",
    selected_clip_id: "",
    new_event_start_ms: "",
    new_event_end_ms: "",
  };

  let state = clone(embeddedState);
  try {
    const restored = JSON.parse(localStorage.getItem(STORAGE_KEY) || "null");
    if (restored && restored.state_version === WORKBENCH_VERSION) {
      state = { ...state, ...restored, identity: { ...state.identity, ...(restored.identity || {}) } };
    }
  } catch (_) {
    // File URLs and private browsing may deny storage. CSV export remains available.
  }

  const video = $("#workbench-video");
  const canvas = $("#keypoint-canvas");
  const ctx = canvas.getContext("2d");
  let activeTab = "events";
  let persistTimer = null;

  function setStatus(message, kind = "") {
    const node = $("#workbench-status");
    node.textContent = message;
    node.className = `status-line ${kind}`;
  }

  function persist(message = "草稿已保存到浏览器本地存储") {
    clearTimeout(persistTimer);
    persistTimer = setTimeout(() => {
      try {
        localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
        setStatus(message, "good");
      } catch (_) {
        setStatus("本地存储不可用；请立即导出 CSV 避免丢失。", "error");
      }
    }, 120);
  }

  function csvCell(value) {
    const text = value === undefined || value === null ? "" : String(value);
    return /[",\r\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
  }

  function toCsv(headers, rows) {
    return "\ufeff" + [headers, ...rows.map((row) => headers.map((name) => row[name] ?? ""))]
      .map((cells) => cells.map(csvCell).join(","))
      .join("\r\n") + "\r\n";
  }

  function parseCsv(text) {
    text = text.replace(/^\ufeff/, "");
    const records = [];
    let row = [], field = "", quoted = false;
    for (let i = 0; i < text.length; i += 1) {
      const char = text[i];
      if (quoted) {
        if (char === '"' && text[i + 1] === '"') { field += '"'; i += 1; }
        else if (char === '"') quoted = false;
        else field += char;
      } else if (char === '"') quoted = true;
      else if (char === ",") { row.push(field); field = ""; }
      else if (char === "\n") {
        row.push(field.replace(/\r$/, ""));
        if (row.some((item) => item !== "")) records.push(row);
        row = []; field = "";
      } else field += char;
    }
    if (field || row.length) { row.push(field.replace(/\r$/, "")); records.push(row); }
    if (!records.length) return { headers: [], rows: [] };
    const headers = records[0];
    return {
      headers,
      rows: records.slice(1).map((cells) => Object.fromEntries(headers.map((name, index) => [name, cells[index] ?? ""]))),
    };
  }

  function exportFiles() {
    return {
      "event-annotations.csv": toCsv(BOOT.headers.events, state.events),
      "keypoint-annotations.csv": toCsv(BOOT.headers.keypoints, state.keypoints),
      "semantic-annotations.csv": toCsv(BOOT.headers.semantics, state.semantics),
      "full-video-review-completion.csv": toCsv(BOOT.headers.reviews, state.reviews),
    };
  }

  function download(name, content, type = "text/csv;charset=utf-8") {
    const link = document.createElement("a");
    link.href = URL.createObjectURL(new Blob([content], { type }));
    link.download = name;
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(link.href), 5000);
  }

  function normalizeRow(headers, row) {
    return Object.fromEntries(headers.map((name) => [name, row[name] ?? ""]));
  }

  function eventKey(row) { return `${row.video_id}::${row.event_id}`; }
  function keypointKey(row) { return `${row.video_id}::${row.source_frame_index}::${row.joint_name}`; }
  function selectedEvent() { return state.events.find((row) => eventKey(row) === state.selected_event_key) || null; }
  function selectedKeypoint() { return state.keypoints.find((row) => keypointKey(row) === state.selected_keypoint_key) || null; }
  function selectedSemantic() { return state.semantics.find((row) => row.annotation_id === state.selected_semantic_id) || null; }
  function activeVideoInfo() { return BOOT.manifest.videos.find((item) => item.video_id === state.active_video_id); }

  function option(value, label, selected = false) {
    const node = document.createElement("option");
    node.value = value;
    node.textContent = label;
    node.selected = selected;
    return node;
  }

  function setActiveVideo(videoId) {
    if (!BOOT.manifest.videos.some((item) => item.video_id === videoId)) return;
    state.active_video_id = videoId;
    const info = activeVideoInfo();
    video.pause();
    video.src = info.workbench_source;
    video.load();
    $("#active-video-id").textContent = videoId;
    state.selected_event_key = "";
    state.selected_keypoint_key = "";
    state.selected_semantic_id = "";
    state.selected_clip_id = "";
    renderAllEditors();
    persist();
  }

  function seekMs(timestampMs, play = false) {
    const value = Number(timestampMs);
    if (!Number.isFinite(value)) return;
    video.currentTime = Math.max(0, value / 1000);
    if (play) video.play().catch(() => {}); else video.pause();
  }

  function setTab(tabName) {
    activeTab = tabName;
    $$("[data-tab]").forEach((node) => node.classList.toggle("active", node.dataset.tab === tabName));
    $$(".panel[data-panel]").forEach((node) => node.classList.toggle("active", node.dataset.panel === tabName));
    $("#video-stage").classList.toggle("annotating", tabName === "keypoints");
    drawKeypoints();
  }

  function syncIdentity() {
    state.identity.annotator_id = $("#identity-annotator").value.trim();
    state.identity.reviewer_id = $("#identity-reviewer").value.trim();
    state.identity.view_group = $("#identity-view").value.trim();
    persist();
  }

  function requiredPhases(code) { return BOOT.required_phases_by_event[code] || []; }

  function renderEventList() {
    const body = $("#event-list-body");
    body.textContent = "";
    const rows = state.events.filter((row) => row.video_id === state.active_video_id);
    rows.sort((a, b) => Number(a.start_ms || 0) - Number(b.start_ms || 0));
    rows.forEach((row) => {
      const tr = document.createElement("tr");
      tr.dataset.status = row.adjudication_status || "draft";
      tr.classList.toggle("selected", eventKey(row) === state.selected_event_key);
      [row.event_id, row.event_code, row.start_ms, row.end_ms, row.adjudication_status || "draft"].forEach((value) => {
        const td = document.createElement("td"); td.textContent = value; tr.appendChild(td);
      });
      tr.addEventListener("click", () => { saveEventEditor(false); state.selected_event_key = eventKey(row); renderEventList(); renderEventEditor(); });
      body.appendChild(tr);
    });
    $("#event-count").textContent = `${rows.length} 条人工事件草稿`;
  }

  function createEvent() {
    const id = $("#new-event-id").value.trim();
    const code = $("#new-event-code").value;
    const start = $("#new-event-start").value.trim();
    const end = $("#new-event-end").value.trim();
    if (!id || blank(start) || blank(end) || Number(start) >= Number(end)) {
      setStatus("新事件需要唯一 event_id，且 start_ms < end_ms。", "error"); return;
    }
    if (state.events.some((row) => row.video_id === state.active_video_id && row.event_id === id)) {
      setStatus("当前视频中 event_id 已存在。", "error"); return;
    }
    const row = normalizeRow(BOOT.headers.events, {
      video_id: state.active_video_id, event_id: id, event_code: code,
      person_track_id: "1", start_ms: start, end_ms: end,
      annotation_confidence: "", boundary_uncertainty_ms: "",
      view_group: state.identity.view_group, annotator_id: state.identity.annotator_id,
      reviewer_id: state.identity.reviewer_id, adjudication_status: "draft", quality_flags: "",
    });
    state.events.push(row);
    state.selected_event_key = eventKey(row);
    state.new_event_start_ms = ""; state.new_event_end_ms = "";
    $("#new-event-id").value = "";
    renderEventList(); renderEventEditor(); renderNewEventMarks(); renderSemanticList();
    persist("人工事件草稿已新建，尚未 accepted。");
  }

  function renderNewEventMarks() {
    $("#new-event-start").value = state.new_event_start_ms;
    $("#new-event-end").value = state.new_event_end_ms;
  }

  function renderEventEditor() {
    const row = selectedEvent();
    $("#event-editor-empty").classList.toggle("hidden", Boolean(row));
    $("#event-editor").classList.toggle("hidden", !row);
    if (!row) return;
    $("#event-edit-id").value = row.event_id;
    $("#event-edit-code").value = row.event_code;
    $("#event-edit-track").value = row.person_track_id || "1";
    $("#event-edit-start").value = row.start_ms;
    $("#event-edit-end").value = row.end_ms;
    $("#event-edit-confidence").value = row.annotation_confidence;
    $("#event-edit-uncertainty").value = row.boundary_uncertainty_ms;
    $("#event-edit-view").value = row.view_group;
    $("#event-edit-annotator").value = row.annotator_id;
    $("#event-edit-reviewer").value = row.reviewer_id;
    $("#event-edit-status").value = row.adjudication_status || "draft";
    $("#event-edit-flags").value = row.quality_flags;
    const phases = $("#event-phases"); phases.textContent = "";
    requiredPhases(row.event_code).forEach((phase) => {
      const wrap = document.createElement("div"); wrap.className = "phase-row";
      const label = document.createElement("label"); label.textContent = `${phaseLabel(phase)} *`;
      const input = document.createElement("input"); input.type = "number"; input.step = "1"; input.value = row[phase] || ""; input.dataset.phase = phase; label.appendChild(input);
      const mark = document.createElement("button"); mark.type = "button"; mark.textContent = "用当前时间";
      mark.addEventListener("click", () => { input.value = String(nowMs()); row[phase] = input.value; persist(); });
      const seek = document.createElement("button"); seek.type = "button"; seek.textContent = "定位";
      seek.addEventListener("click", () => seekMs(input.value));
      wrap.append(label, mark, seek); phases.appendChild(wrap);
    });
  }

  function saveEventEditor(doPersist = true) {
    const row = selectedEvent(); if (!row) return;
    const oldKey = eventKey(row);
    Object.assign(row, {
      event_id: $("#event-edit-id").value.trim(), event_code: $("#event-edit-code").value,
      person_track_id: $("#event-edit-track").value.trim(), start_ms: $("#event-edit-start").value.trim(),
      end_ms: $("#event-edit-end").value.trim(), annotation_confidence: $("#event-edit-confidence").value.trim(),
      boundary_uncertainty_ms: $("#event-edit-uncertainty").value.trim(), view_group: $("#event-edit-view").value.trim(),
      annotator_id: $("#event-edit-annotator").value.trim(), reviewer_id: $("#event-edit-reviewer").value.trim(),
      adjudication_status: $("#event-edit-status").value, quality_flags: $("#event-edit-flags").value.trim(),
    });
    $$("[data-phase]", $("#event-phases")).forEach((input) => { row[input.dataset.phase] = input.value.trim(); });
    if (row.adjudication_status === "accepted") {
      const problems = validateEventRow(row);
      if (state.events.some((item) => item !== row && eventKey(item) === eventKey(row) && item.adjudication_status === "accepted")) {
        problems.push("与另一条 accepted event_id 重复");
      }
      if (problems.length) {
        row.adjudication_status = "draft";
        $("#event-edit-status").value = "draft";
        setStatus(`不能 accepted：${problems.join("；")}`, "error");
      }
    }
    state.selected_event_key = eventKey(row);
    if (oldKey !== state.selected_event_key && state.events.some((item) => item !== row && eventKey(item) === state.selected_event_key)) {
      setStatus("event_id 与当前视频的其他事件重复。", "error");
    }
    if (doPersist) { renderEventList(); renderSemanticList(); persist(); }
  }

  function deleteEvent() {
    const row = selectedEvent(); if (!row || !window.confirm(`删除人工事件草稿 ${row.event_id}？`)) return;
    state.events = state.events.filter((item) => item !== row);
    state.selected_event_key = "";
    renderEventList(); renderEventEditor(); renderSemanticList(); persist();
  }

  function markReviewComplete() {
    syncIdentity();
    const annotator = state.identity.annotator_id;
    if (!annotator) { setStatus("先填写页顶 annotator_id。", "error"); return; }
    if (!$("#review-confirm").checked) { setStatus("必须显式确认已完整观看当前视频。", "error"); return; }
    let row = state.reviews.find((item) => item.video_id === state.active_video_id && item.annotator_id === annotator);
    if (!row) { row = normalizeRow(BOOT.headers.reviews, { video_id: state.active_video_id, annotator_id: annotator }); state.reviews.push(row); }
    row.full_video_review_completed = "true";
    row.reviewed_at = new Date().toISOString();
    row.notes = $("#review-notes").value.trim();
    renderReviewList(); persist("全视频完整审阅记录已保存。");
  }

  function renderReviewList() {
    const body = $("#review-list-body"); body.textContent = "";
    state.reviews.filter((row) => row.video_id === state.active_video_id).forEach((row) => {
      const tr = document.createElement("tr");
      [row.annotator_id, row.full_video_review_completed, row.reviewed_at, row.notes].forEach((value) => { const td = document.createElement("td"); td.textContent = value; tr.appendChild(td); });
      body.appendChild(tr);
    });
  }

  function activeCandidates() { return BOOT.manifest.pilot_keypoint_events.filter((item) => item.video_id === state.active_video_id); }
  function keypointRows() {
    return state.keypoints.filter((row) => row.video_id === state.active_video_id && (!state.selected_clip_id || String(row.source_clip_ids).split(";").includes(state.selected_clip_id)));
  }

  function renderClipSelector() {
    const select = $("#keypoint-clip"); select.textContent = "";
    select.appendChild(option("", "所有试点关键点任务", !state.selected_clip_id));
    activeCandidates().forEach((item) => select.appendChild(option(item.blind_clip_id, `${item.blind_clip_id} | ${item.event_code} | ${item.candidate_start_ms}-${item.candidate_end_ms} ms`, state.selected_clip_id === item.blind_clip_id)));
    const candidate = activeCandidates().find((item) => item.blind_clip_id === state.selected_clip_id);
    $("#candidate-context").textContent = candidate
      ? `候选上下文（不是真值）：${candidate.event_code} ${candidate.candidate_start_ms}-${candidate.candidate_end_ms} ms；${candidate.candidate_event_id}`
      : "未选候选片段。候选仅用于密集关键点工作量抽样，不得当作事件真值。";
  }

  function renderFrameSelector() {
    const select = $("#keypoint-frame"); select.textContent = "";
    const frames = new Map();
    keypointRows().forEach((row) => frames.set(`${row.source_frame_index}::${row.timestamp_ms}`, row));
    for (const [key, row] of frames) select.appendChild(option(key, `frame ${row.source_frame_index} | ${row.timestamp_ms} ms`));
    const current = selectedKeypoint();
    if (current && current.video_id === state.active_video_id) select.value = `${current.source_frame_index}::${current.timestamp_ms}`;
    else if (select.options.length) select.value = select.options[0].value;
    if (!current && select.value) selectFrame(select.value, false);
  }

  function selectFrame(frameKey, doSeek = true) {
    saveKeypointEditor(false);
    const [frame] = frameKey.split("::");
    const rows = keypointRows().filter((row) => String(row.source_frame_index) === frame);
    const preferred = rows.find((row) => row.joint_name === selectedKeypoint()?.joint_name) || rows[0];
    state.selected_keypoint_key = preferred ? keypointKey(preferred) : "";
    if (preferred && doSeek) seekMs(preferred.timestamp_ms);
    renderKeypointEditor(); drawKeypoints();
  }

  function renderKeypointEditor() {
    const row = selectedKeypoint();
    $("#keypoint-editor-empty").classList.toggle("hidden", Boolean(row));
    $("#keypoint-editor").classList.toggle("hidden", !row);
    if (!row) { renderKeypointProgress(); return; }
    $("#joint-name").textContent = row.joint_name;
    $("#joint-frame-meta").textContent = `frame ${row.source_frame_index} | ${row.timestamp_ms} ms | ${row.source_clip_ids}`;
    $("#joint-visible").value = row.visible || "";
    $("#joint-x").value = row.x_normalized || "";
    $("#joint-y").value = row.y_normalized || "";
    $("#joint-reason").value = row.visibility_reason || "";
    $("#joint-view").value = row.view_group || "";
    $("#joint-annotator").value = row.annotator_id || "";
    $("#joint-reviewer").value = row.reviewer_id || "";
    $("#joint-status").value = row.adjudication_status || "draft";
    renderKeypointProgress();
  }

  function saveKeypointEditor(doPersist = true) {
    const row = selectedKeypoint(); if (!row || $("#keypoint-editor").classList.contains("hidden")) return;
    Object.assign(row, {
      visible: $("#joint-visible").value, x_normalized: $("#joint-x").value.trim(), y_normalized: $("#joint-y").value.trim(),
      visibility_reason: $("#joint-reason").value.trim(), view_group: $("#joint-view").value.trim(),
      annotator_id: $("#joint-annotator").value.trim(), reviewer_id: $("#joint-reviewer").value.trim(),
      adjudication_status: $("#joint-status").value,
    });
    if (row.visible === "false") { row.x_normalized = ""; row.y_normalized = ""; $("#joint-x").value = ""; $("#joint-y").value = ""; }
    if (row.adjudication_status === "accepted") {
      const problem = rowKeypointError(row);
      if (problem) {
        row.adjudication_status = "draft";
        $("#joint-status").value = "draft";
        setStatus(`不能 accepted 该关节：${problem}`, "error");
      }
    }
    if (doPersist) { drawKeypoints(); renderKeypointProgress(); persist(); }
  }

  function currentFrameRows() {
    const row = selectedKeypoint(); if (!row) return [];
    return state.keypoints.filter((item) => item.video_id === row.video_id && String(item.source_frame_index) === String(row.source_frame_index));
  }

  function rowKeypointError(row) {
    if (!["true", "false"].includes(row.visible)) return "visible 未设置";
    if (row.visible === "true") {
      const x = Number(row.x_normalized), y = Number(row.y_normalized);
      if (!Number.isFinite(x) || !Number.isFinite(y) || x < 0 || x > 1 || y < 0 || y > 1) return "可见点坐标必须在 0..1";
    } else if (!row.visibility_reason) return "不可见点需要 visibility_reason";
    if (!row.view_group || !row.annotator_id || !row.reviewer_id) return "缺少视角/标注者/复核者";
    return "";
  }

  function acceptCurrentFrame() {
    saveKeypointEditor(false);
    const rows = currentFrameRows();
    if (!rows.length) return;
    const missing = rows.filter(rowKeypointError);
    if (missing.length) { setStatus(`当前帧仍有 ${missing.length} 个关节未完成，不能整帧 accepted。`, "error"); return; }
    rows.forEach((row) => { row.adjudication_status = "accepted"; });
    renderKeypointEditor(); persist("当前帧所有关节已显式标记 accepted。");
  }

  function applyIdentityToFrame() {
    syncIdentity();
    if (!state.identity.annotator_id || !state.identity.reviewer_id || !state.identity.view_group) {
      setStatus("先在页顶填完标注者、复核者和视角。", "error"); return;
    }
    currentFrameRows().forEach((row) => {
      row.annotator_id = state.identity.annotator_id; row.reviewer_id = state.identity.reviewer_id; row.view_group = state.identity.view_group;
    });
    renderKeypointEditor(); persist();
  }

  function moveKeypoint(delta) {
    saveKeypointEditor(false);
    const rows = keypointRows().slice().sort((a, b) => Number(a.timestamp_ms) - Number(b.timestamp_ms) || BOOT.keypoint_joints.indexOf(a.joint_name) - BOOT.keypoint_joints.indexOf(b.joint_name));
    if (!rows.length) return;
    let index = rows.findIndex((row) => keypointKey(row) === state.selected_keypoint_key);
    index = Math.max(0, Math.min(rows.length - 1, index + delta));
    state.selected_keypoint_key = keypointKey(rows[index]);
    $("#keypoint-frame").value = `${rows[index].source_frame_index}::${rows[index].timestamp_ms}`;
    seekMs(rows[index].timestamp_ms); renderKeypointEditor(); drawKeypoints(); persist();
  }

  function renderKeypointProgress() {
    const rows = keypointRows();
    const reviewed = rows.filter((row) => ["true", "false"].includes(row.visible)).length;
    const accepted = rows.filter((row) => row.adjudication_status === "accepted").length;
    $("#keypoint-progress").textContent = `${reviewed}/${rows.length} 已标注，${accepted}/${rows.length} accepted`;
  }

  function canvasVideoRect() {
    const width = canvas.width || 1, height = canvas.height || 1;
    const videoRatio = (video.videoWidth || 16) / (video.videoHeight || 9);
    if (width / height > videoRatio) {
      const contentWidth = height * videoRatio;
      return { x: (width - contentWidth) / 2, y: 0, width: contentWidth, height };
    }
    const contentHeight = width / videoRatio;
    return { x: 0, y: (height - contentHeight) / 2, width, height: contentHeight };
  }

  function drawKeypoints() {
    const stage = $("#video-stage");
    const width = Math.max(1, Math.round(stage.clientWidth));
    const height = Math.max(1, Math.round(stage.clientHeight));
    if (canvas.width !== width) canvas.width = width;
    if (canvas.height !== height) canvas.height = height;
    ctx.clearRect(0, 0, width, height);
    if (activeTab !== "keypoints") return;
    const content = canvasVideoRect();
    currentFrameRows().filter((row) => row.visible === "true" && !blank(row.x_normalized) && !blank(row.y_normalized)).forEach((row) => {
      const x = content.x + Number(row.x_normalized) * content.width;
      const y = content.y + Number(row.y_normalized) * content.height;
      const selected = keypointKey(row) === state.selected_keypoint_key;
      ctx.beginPath(); ctx.arc(x, y, selected ? 8 : 5, 0, Math.PI * 2);
      ctx.fillStyle = selected ? "#ffcc00" : "#35e68a"; ctx.fill();
      ctx.strokeStyle = "#12251a"; ctx.lineWidth = 2; ctx.stroke();
      ctx.font = "14px system-ui"; ctx.fillStyle = "white"; ctx.strokeStyle = "black"; ctx.lineWidth = 3;
      ctx.strokeText(row.joint_name, x + 9, y - 7); ctx.fillText(row.joint_name, x + 9, y - 7);
    });
  }

  function canvasClick(event) {
    if (activeTab !== "keypoints") return;
    const row = selectedKeypoint(); if (!row) return;
    const bounds = canvas.getBoundingClientRect();
    const content = canvasVideoRect();
    const canvasX = (event.clientX - bounds.left) * canvas.width / bounds.width;
    const canvasY = (event.clientY - bounds.top) * canvas.height / bounds.height;
    if (canvasX < content.x || canvasX > content.x + content.width || canvasY < content.y || canvasY > content.y + content.height) {
      setStatus("请点击实际视频画面，不要点击黑色留白区。", "error"); return;
    }
    const x = Math.max(0, Math.min(1, (canvasX - content.x) / content.width));
    const y = Math.max(0, Math.min(1, (canvasY - content.y) / content.height));
    row.visible = "true"; row.x_normalized = x.toFixed(6); row.y_normalized = y.toFixed(6); row.visibility_reason = "";
    if (!row.view_group) row.view_group = state.identity.view_group;
    if (!row.annotator_id) row.annotator_id = state.identity.annotator_id;
    if (!row.reviewer_id) row.reviewer_id = state.identity.reviewer_id;
    renderKeypointEditor(); drawKeypoints(); persist("人工点击坐标已保存；尚需显式 accepted。");
  }

  function semanticRows() { return state.semantics.filter((row) => row.video_id === state.active_video_id); }
  function renderSemanticList() {
    const body = $("#semantic-list-body"); body.textContent = "";
    semanticRows().forEach((row) => {
      const tr = document.createElement("tr"); tr.dataset.status = row.adjudication_status || "draft";
      tr.classList.toggle("selected", row.annotation_id === state.selected_semantic_id);
      [row.indicator_id, row.semantic_key, row.event_id || "未绑定", row.observable || "未定", row.adjudication_status || "draft"].forEach((value) => { const td = document.createElement("td"); td.textContent = value; tr.appendChild(td); });
      tr.addEventListener("click", () => { saveSemanticEditor(false); state.selected_semantic_id = row.annotation_id; renderSemanticList(); renderSemanticEditor(); });
      body.appendChild(tr);
    });
    const accepted = semanticRows().filter((row) => row.adjudication_status === "accepted").length;
    $("#semantic-progress").textContent = `${accepted}/${semanticRows().length} accepted`;
  }

  function renderSemanticEditor() {
    const row = selectedSemantic();
    $("#semantic-editor-empty").classList.toggle("hidden", Boolean(row));
    $("#semantic-editor").classList.toggle("hidden", !row);
    if (!row) return;
    $("#semantic-title").textContent = `${row.indicator_id} / ${row.semantic_key}`;
    $("#semantic-meta").textContent = `${row.semantic_type} | ${row.blind_clip_id} | candidate ${row.candidate_event_id} (仅上下文)`;
    const events = state.events.filter((event) => event.video_id === row.video_id && event.event_code === row.indicator_id.split("-")[0]);
    const eventSelect = $("#semantic-event"); eventSelect.textContent = ""; eventSelect.appendChild(option("", "选择人工事件（不能选模型候选）", !row.event_id));
    events.forEach((event) => eventSelect.appendChild(option(event.event_id, `${event.event_id} | ${event.start_ms}-${event.end_ms} | ${event.adjudication_status || "draft"}`, row.event_id === event.event_id)));
    $("#semantic-observable").value = row.observable || "";
    $("#semantic-confidence").value = row.annotation_confidence || "";
    $("#semantic-annotator").value = row.annotator_id || "";
    $("#semantic-reviewer").value = row.reviewer_id || "";
    $("#semantic-status").value = row.adjudication_status || "draft";
    $("#semantic-null-reason").value = row.null_reason || "";
    ["direction_deg", "coordinate_frame", "side", "timestamp_ms", "interval_start_ms", "interval_end_ms", "contact_state", "camera_motion_observed", "camera_audit_method"].forEach((name) => {
      const node = $(`[data-semantic-field="${name}"]`); if (node) node.value = row[name] || "";
    });
    $$("[data-semantic-group]").forEach((node) => node.classList.toggle("hidden", node.dataset.semanticGroup !== row.semantic_type || row.observable !== "true"));
    $("#semantic-null-group").classList.toggle("hidden", row.observable !== "false");
  }

  function saveSemanticEditor(doPersist = true) {
    const row = selectedSemantic(); if (!row || $("#semantic-editor").classList.contains("hidden")) return;
    Object.assign(row, {
      event_id: $("#semantic-event").value, observable: $("#semantic-observable").value,
      annotation_confidence: $("#semantic-confidence").value.trim(), annotator_id: $("#semantic-annotator").value.trim(),
      reviewer_id: $("#semantic-reviewer").value.trim(), adjudication_status: $("#semantic-status").value,
      null_reason: $("#semantic-null-reason").value.trim(),
    });
    ["direction_deg", "coordinate_frame", "side", "timestamp_ms", "interval_start_ms", "interval_end_ms", "contact_state", "camera_motion_observed", "camera_audit_method"].forEach((name) => {
      const node = $(`[data-semantic-field="${name}"]`); if (node) row[name] = node.value.trim();
    });
    if (row.observable === "false") {
      ["direction_deg", "coordinate_frame", "side", "timestamp_ms", "interval_start_ms", "interval_end_ms", "contact_state", "camera_motion_observed", "camera_audit_method"].forEach((name) => { row[name] = ""; });
    } else if (row.observable === "true") row.null_reason = "";
    if (row.adjudication_status === "accepted") {
      const problem = semanticError(row);
      if (problem) {
        row.adjudication_status = "draft";
        $("#semantic-status").value = "draft";
        setStatus(`不能 accepted 该语义任务：${problem}`, "error");
      }
    }
    if (doPersist) { renderSemanticList(); renderSemanticEditor(); persist(); }
  }

  function applySemanticIdentity() {
    syncIdentity(); const row = selectedSemantic(); if (!row) return;
    row.annotator_id = state.identity.annotator_id; row.reviewer_id = state.identity.reviewer_id;
    renderSemanticEditor(); persist();
  }

  function moveSemantic(delta) {
    saveSemanticEditor(false); const rows = semanticRows(); if (!rows.length) return;
    let index = rows.findIndex((row) => row.annotation_id === state.selected_semantic_id);
    index = Math.max(0, Math.min(rows.length - 1, index + delta));
    state.selected_semantic_id = rows[index].annotation_id; renderSemanticList(); renderSemanticEditor(); persist();
  }

  function validateEventRow(row) {
    const errors = [];
      const start = Number(row.start_ms), end = Number(row.end_ms), confidence = Number(row.annotation_confidence), uncertainty = Number(row.boundary_uncertainty_ms);
    if (!row.event_id || !BOOT.event_codes.includes(row.event_code) || !Number.isFinite(start) || !Number.isFinite(end) || start >= end) errors.push("边界或代码无效");
    if (!Number.isInteger(Number(row.person_track_id)) || Number(row.person_track_id) < 1) errors.push("person_track_id 必须为正整数");
    requiredPhases(row.event_code).forEach((phase) => { const value = Number(row[phase]); if (blank(row[phase]) || !Number.isFinite(value) || value < start || value > end) errors.push(`${phase} 必须在事件内`); });
    if (blank(row.annotation_confidence) || !Number.isFinite(confidence) || confidence < 0 || confidence > 1) errors.push("annotation_confidence 必须在 0..1");
    if (blank(row.boundary_uncertainty_ms) || !Number.isFinite(uncertainty) || uncertainty < 0) errors.push("boundary_uncertainty_ms 必须为非负数");
    if (!row.view_group || !row.annotator_id || !row.reviewer_id) errors.push("缺少视角/标注者/复核者");
    return errors;
  }

  function eventErrors() {
    const errors = [];
    const seen = new Set();
    state.events.filter((row) => row.adjudication_status === "accepted").forEach((row) => {
      const key = eventKey(row);
      if (seen.has(key)) errors.push(`重复 accepted event: ${key}`); seen.add(key);
      validateEventRow(row).forEach((error) => errors.push(`事件 ${row.event_id || "(无ID)"}: ${error}`));
    });
    return errors;
  }

  function semanticError(row) {
    const event = state.events.find((item) => item.video_id === row.video_id && item.event_id === row.event_id && item.adjudication_status === "accepted");
    if (!event) return "未绑定 accepted 人工事件";
    if (!["true", "false"].includes(row.observable)) return "observable 未设置";
    const confidence = Number(row.annotation_confidence);
    if (!row.annotator_id || !row.reviewer_id || blank(row.annotation_confidence)) return "缺少标注者/复核者/confidence";
    if (!Number.isFinite(confidence) || confidence < 0 || confidence > 1) return "annotation_confidence 必须在 0..1";
    if (row.observable === "false") return row.null_reason ? "" : "observable=false 需要 null_reason";
    if (row.semantic_type === "direction" && (blank(row.direction_deg) || !Number.isFinite(Number(row.direction_deg)) || Number(row.direction_deg) < -180 || Number(row.direction_deg) > 180 || !["image_plane", "court_plane"].includes(row.coordinate_frame))) return "direction 值必须在 -180..180 且指定坐标系";
    if (row.semantic_type === "side" && !["left", "right", "bilateral"].includes(row.side)) return "side 值无效";
    if (row.semantic_type === "timestamp" && (blank(row.timestamp_ms) || Number(row.timestamp_ms) < Number(event.start_ms) || Number(row.timestamp_ms) > Number(event.end_ms))) return "timestamp 必须在人工事件内";
    if (row.semantic_type === "interval" && (blank(row.interval_start_ms) || blank(row.interval_end_ms) || Number(row.interval_start_ms) < Number(event.start_ms) || Number(row.interval_start_ms) >= Number(row.interval_end_ms) || Number(row.interval_end_ms) > Number(event.end_ms))) return "interval 必须有序且在人工事件内";
    if (row.semantic_type === "contact_state" && !["contact", "airborne", "settled"].includes(row.contact_state)) return "contact_state 值无效";
    if (row.semantic_type === "camera_audit" && (!['true', 'false'].includes(row.camera_motion_observed) || blank(row.camera_audit_method))) return "camera_audit 不完整";
    return "";
  }

  function validateWorkbench() {
    saveEventEditor(false); saveKeypointEditor(false); saveSemanticEditor(false);
    const errors = eventErrors();
    state.keypoints.filter((row) => row.adjudication_status === "accepted").forEach((row) => { const error = rowKeypointError(row); if (error) errors.push(`keypoint ${keypointKey(row)}: ${error}`); });
    state.semantics.filter((row) => row.adjudication_status === "accepted").forEach((row) => { const error = semanticError(row); if (error) errors.push(`semantic ${row.annotation_id}: ${error}`); });
    const list = $("#validation-list"); list.textContent = "";
    if (!errors.length) { const li = document.createElement("li"); li.textContent = "工作台快速检查通过；仍必须运行 Python 编译器作为最终契约校验。"; list.appendChild(li); setStatus("快速检查通过。", "good"); }
    else { errors.forEach((error) => { const li = document.createElement("li"); li.textContent = error; list.appendChild(li); }); setStatus(`发现 ${errors.length} 个 accepted 记录问题。`, "error"); }
    return errors;
  }

  async function writeFilesToDirectory() {
    const errors = validateWorkbench();
    if (errors.length && !window.confirm(`有 ${errors.length} 个 accepted 记录校验问题，仍写出草稿？`)) return;
    if (!("showDirectoryPicker" in window)) { setStatus("当前浏览器不支持目录写入，请使用“下载全部 CSV”。", "error"); return; }
    try {
      const directory = await window.showDirectoryPicker({ mode: "readwrite" });
      for (const [name, content] of Object.entries(exportFiles())) {
        const handle = await directory.getFileHandle(name, { create: true });
        const writable = await handle.createWritable(); await writable.write(content); await writable.close();
      }
      setStatus("四份 CSV 已写入你选择的目录。下一步运行 Python 编译器。", "good");
    } catch (error) { if (error.name !== "AbortError") setStatus(`写入失败：${error.message}`, "error"); }
  }

  function downloadAll() {
    const errors = validateWorkbench();
    if (errors.length && !window.confirm(`有 ${errors.length} 个 accepted 记录校验问题，仍下载草稿？`)) return;
    Object.entries(exportFiles()).forEach(([name, content]) => download(name, content));
    setStatus("已下载四份 CSV；请覆盖真值包同名文件后运行编译器。", "good");
  }

  async function importCsvFiles(files) {
    const mapping = {
      "event-annotations.csv": ["events", BOOT.headers.events],
      "keypoint-annotations.csv": ["keypoints", BOOT.headers.keypoints],
      "semantic-annotations.csv": ["semantics", BOOT.headers.semantics],
      "full-video-review-completion.csv": ["reviews", BOOT.headers.reviews],
    };
    const imported = [];
    for (const file of files) {
      if (!mapping[file.name]) continue;
      const [stateKey, headers] = mapping[file.name];
      const parsed = parseCsv(await file.text());
      const missing = headers.filter((name) => !parsed.headers.includes(name));
      if (missing.length) { setStatus(`${file.name} 缺少列：${missing.join(", ")}`, "error"); return; }
      state[stateKey] = parsed.rows.map((row) => normalizeRow(headers, row)); imported.push(file.name);
    }
    if (!imported.length) { setStatus("未选择受支持的真值包 CSV。", "error"); return; }
    state.selected_event_key = ""; state.selected_keypoint_key = ""; state.selected_semantic_id = "";
    renderAllEditors(); persist(`已导入：${imported.join(", ")}`);
  }

  function resetState() {
    if (!window.confirm("清空工作台本地草稿，恢复 review.html 内嵌的 CSV 状态？此操作无法撤销。")) return;
    state = clone(embeddedState); localStorage.removeItem(STORAGE_KEY);
    $("#identity-annotator").value = ""; $("#identity-reviewer").value = ""; $("#identity-view").value = state.identity.view_group;
    $("#video-select").value = state.active_video_id; setActiveVideo(state.active_video_id); setStatus("已恢复内嵌空白/已导入模板。", "good");
  }

  function renderAllEditors() {
    renderNewEventMarks(); renderEventList(); renderEventEditor(); renderReviewList();
    renderClipSelector(); renderFrameSelector(); renderKeypointEditor(); renderSemanticList(); renderSemanticEditor(); drawKeypoints();
  }

  function bind() {
    const videoSelect = $("#video-select");
    BOOT.manifest.videos.forEach((item) => videoSelect.appendChild(option(item.video_id, item.video_id, item.video_id === state.active_video_id)));
    videoSelect.addEventListener("change", () => setActiveVideo(videoSelect.value));
    $("#identity-annotator").value = state.identity.annotator_id; $("#identity-reviewer").value = state.identity.reviewer_id; $("#identity-view").value = state.identity.view_group;
    ["#identity-annotator", "#identity-reviewer", "#identity-view"].forEach((selector) => $(selector).addEventListener("change", syncIdentity));
    $$("[data-tab]").forEach((node) => node.addEventListener("click", () => setTab(node.dataset.tab)));
    video.addEventListener("timeupdate", () => { $("#time-readout").textContent = `${nowMs()} ms`; });
    video.addEventListener("loadedmetadata", drawKeypoints); video.addEventListener("seeked", drawKeypoints); window.addEventListener("resize", drawKeypoints);
    $("#play-pause").addEventListener("click", () => video.paused ? video.play().catch(() => {}) : video.pause());
    $$("[data-step-ms]").forEach((node) => node.addEventListener("click", () => seekMs(nowMs() + Number(node.dataset.stepMs))));
    $("#mark-new-start").addEventListener("click", () => { state.new_event_start_ms = String(nowMs()); $("#new-event-start").value = state.new_event_start_ms; persist(); });
    $("#mark-new-end").addEventListener("click", () => { state.new_event_end_ms = String(nowMs()); $("#new-event-end").value = state.new_event_end_ms; persist(); });
    $("#new-event-start").addEventListener("change", () => { state.new_event_start_ms = $("#new-event-start").value.trim(); persist(); });
    $("#new-event-end").addEventListener("change", () => { state.new_event_end_ms = $("#new-event-end").value.trim(); persist(); });
    $("#add-event").addEventListener("click", createEvent);
    $("#save-event").addEventListener("click", () => saveEventEditor(true));
    $("#delete-event").addEventListener("click", deleteEvent);
    $("#event-mark-start").addEventListener("click", () => { $("#event-edit-start").value = String(nowMs()); saveEventEditor(true); });
    $("#event-mark-end").addEventListener("click", () => { $("#event-edit-end").value = String(nowMs()); saveEventEditor(true); });
    $("#event-seek-start").addEventListener("click", () => seekMs($("#event-edit-start").value));
    $("#event-seek-end").addEventListener("click", () => seekMs($("#event-edit-end").value));
    $("#event-edit-code").addEventListener("change", () => { saveEventEditor(false); renderEventEditor(); });
    $("#event-edit-status").addEventListener("change", () => saveEventEditor(true));
    $("#mark-review-complete").addEventListener("click", markReviewComplete);
    $("#keypoint-clip").addEventListener("change", () => { state.selected_clip_id = $("#keypoint-clip").value; const candidate = activeCandidates().find((item) => item.blind_clip_id === state.selected_clip_id); if (candidate) seekMs(candidate.candidate_start_ms); state.selected_keypoint_key = ""; renderClipSelector(); renderFrameSelector(); renderKeypointEditor(); persist(); });
    $("#keypoint-frame").addEventListener("change", () => selectFrame($("#keypoint-frame").value));
    $("#joint-visible").addEventListener("change", () => { saveKeypointEditor(true); renderKeypointEditor(); });
    ["#joint-x", "#joint-y", "#joint-reason", "#joint-view", "#joint-annotator", "#joint-reviewer", "#joint-status"].forEach((selector) => $(selector).addEventListener("change", () => saveKeypointEditor(true)));
    $("#joint-invisible").addEventListener("click", () => { const row = selectedKeypoint(); if (!row) return; row.visible = "false"; row.x_normalized = ""; row.y_normalized = ""; renderKeypointEditor(); persist(); });
    $("#joint-prev").addEventListener("click", () => moveKeypoint(-1)); $("#joint-next").addEventListener("click", () => moveKeypoint(1));
    $("#apply-frame-identity").addEventListener("click", applyIdentityToFrame); $("#accept-frame").addEventListener("click", acceptCurrentFrame);
    canvas.addEventListener("click", canvasClick);
    ["#semantic-event", "#semantic-observable", "#semantic-confidence", "#semantic-annotator", "#semantic-reviewer", "#semantic-status", "#semantic-null-reason"].forEach((selector) => $(selector).addEventListener("change", () => { saveSemanticEditor(true); renderSemanticEditor(); }));
    $$('[data-semantic-field]').forEach((node) => node.addEventListener("change", () => saveSemanticEditor(true)));
    $$("[data-mark-semantic]").forEach((node) => node.addEventListener("click", () => { const input = $(`[data-semantic-field="${node.dataset.markSemantic}"]`); input.value = String(nowMs()); saveSemanticEditor(true); }));
    $("#apply-semantic-identity").addEventListener("click", applySemanticIdentity); $("#semantic-prev").addEventListener("click", () => moveSemantic(-1)); $("#semantic-next").addEventListener("click", () => moveSemantic(1));
    $("#validate-workbench").addEventListener("click", validateWorkbench); $("#write-directory").addEventListener("click", writeFilesToDirectory); $("#download-all").addEventListener("click", downloadAll);
    $("#import-csv").addEventListener("change", (event) => importCsvFiles(Array.from(event.target.files || []))); $("#reset-workbench").addEventListener("click", resetState);
    $$('[data-download]').forEach((node) => node.addEventListener("click", () => { const files = exportFiles(); download(node.dataset.download, files[node.dataset.download]); }));
  }

  function applyExecutionAuthorizationGate() {
    if (BOOT.safety?.annotation_execution_authorized === true) {
      document.body.dataset.annotationExecutionAuthorized = "true";
      return;
    }
    document.body.dataset.annotationExecutionAuthorized = "false";
    $$('button, input, select, textarea').forEach((node) => { node.disabled = true; });
    ["#video-select", "#play-pause", "[data-step-ms]", "[data-tab]"].forEach(
      (selector) => $$(selector).forEach((node) => { node.disabled = false; }),
    );
    canvas.style.pointerEvents = "none";
    setStatus(
      "PRIVATE TECHNICAL TEMPLATE：没有外部协议回执和独立 authorized handoff；标注、接受、导入与导出已禁用。",
      "error",
    );
  }

  bind();
  setActiveVideo(state.active_video_id);
  setTab("events");
  $("#workbench-version").textContent = WORKBENCH_VERSION;
  applyExecutionAuthorizationGate();
})();
