(function () {
  "use strict";

  const boot = JSON.parse(document.getElementById("small-roi-truth-bootstrap").textContent);
  const $ = (selector) => document.querySelector(selector);
  const video = $("#video");
  const canvas = $("#canvas");
  const context = canvas.getContext("2d");
  const storageKey = `rallymate-small-roi-truth:${boot.pack_version}:${boot.video_id}`;
  const taskByKey = new Map(boot.tasks.map((task) => [`${task.source_frame_index}:${task.joint_name}`, task]));
  const frameByIndex = new Map(boot.frames.map((frame) => [String(frame.source_frame_index), frame]));
  let sessions = {};
  let imported = new Map();
  let adjudications = {};
  let crop = null;

  try {
    const saved = JSON.parse(localStorage.getItem(storageKey) || "null");
    if (saved && saved.version === "1.0.0") {
      sessions = saved.sessions || {};
      adjudications = saved.adjudications || {};
    }
  } catch (_) {
    // CSV export remains the durable path when localStorage is unavailable.
  }

  function identity() { return $("#identity").value.trim(); }
  function mode() { return $("#mode").value; }
  function currentFrame() { return frameByIndex.get($("#frame").value); }
  function currentTask() { return taskByKey.get(`${$("#frame").value}:${$("#joint").value}`); }
  function session() {
    const id = identity();
    if (!sessions[id]) sessions[id] = {};
    return sessions[id];
  }
  function persist() {
    try {
      localStorage.setItem(storageKey, JSON.stringify({ version: "1.0.0", sessions, adjudications }));
    } catch (_) {
      setStatus("浏览器草稿无法保存，请立即导出 CSV。", true);
    }
  }
  function setStatus(message, error = false) {
    const node = $("#status");
    node.textContent = message;
    node.className = error ? "error" : "good";
  }
  function sanitize(value) { return value.replace(/[^A-Za-z0-9_.-]+/g, "-"); }
  function csvCell(value) {
    const text = value === null || value === undefined ? "" : String(value);
    return /[",\r\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
  }
  function toCsv(fields, rows) {
    return "\ufeff" + [fields, ...rows.map((row) => fields.map((field) => row[field] ?? ""))]
      .map((row) => row.map(csvCell).join(",")).join("\r\n") + "\r\n";
  }
  function parseCsv(text) {
    text = text.replace(/^\ufeff/, "");
    const rows = [];
    let row = [], value = "", quoted = false;
    for (let index = 0; index < text.length; index += 1) {
      const char = text[index];
      if (quoted) {
        if (char === '"' && text[index + 1] === '"') { value += '"'; index += 1; }
        else if (char === '"') quoted = false;
        else value += char;
      } else if (char === '"') quoted = true;
      else if (char === ",") { row.push(value); value = ""; }
      else if (char === "\n") {
        row.push(value.replace(/\r$/, ""));
        if (row.some((item) => item !== "")) rows.push(row);
        row = []; value = "";
      } else value += char;
    }
    if (value || row.length) { row.push(value.replace(/\r$/, "")); rows.push(row); }
    if (!rows.length) return { fields: [], rows: [] };
    const fields = rows[0];
    return {
      fields,
      rows: rows.slice(1).map((values) => Object.fromEntries(fields.map((field, index) => [field, values[index] || ""]))),
    };
  }
  function download(name, content) {
    const link = document.createElement("a");
    link.href = URL.createObjectURL(new Blob([content], { type: "text/csv;charset=utf-8" }));
    link.download = name;
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(link.href), 5000);
  }
  function visibleRow(task, x, y, sourceIds = []) {
    const now = new Date().toISOString();
    if (mode() === "annotate") {
      const id = identity();
      return {
        annotation_id: `ann-${sanitize(id)}-${sanitize(task.task_id)}`,
        task_id: task.task_id,
        annotator_id: id,
        visible: "true",
        x_normalized: x.toFixed(8),
        y_normalized: y.toFixed(8),
        visibility_reason: "",
        annotated_at: now,
      };
    }
    return {
      adjudication_id: `adj-${sanitize(identity())}-${sanitize(task.task_id)}`,
      task_id: task.task_id,
      source_annotation_ids: sourceIds.join(";"),
      reviewer_id: identity(),
      visible: "true",
      x_normalized: x.toFixed(8),
      y_normalized: y.toFixed(8),
      visibility_reason: "",
      adjudicated_at: now,
      status: "accepted",
    };
  }
  function invisibleRow(task, reason, sourceIds = []) {
    const row = visibleRow(task, 0, 0, sourceIds);
    row.visible = "false";
    row.x_normalized = "";
    row.y_normalized = "";
    row.visibility_reason = reason;
    return row;
  }
  function sourceRows(taskId) {
    return Array.from(imported.values()).filter((row) => row.task_id === taskId);
  }
  function uniqueAnnotators(rows) { return new Set(rows.map((row) => row.annotator_id).filter(Boolean)); }
  function saveRow(row) {
    const task = currentTask();
    if (!task || !identity()) { setStatus("请先填写 annotator / reviewer ID。", true); return; }
    if (mode() === "annotate") session()[task.task_id] = row;
    else {
      const sources = sourceRows(task.task_id);
      if (uniqueAnnotators(sources).size < boot.required_annotators) {
        setStatus("裁决前必须导入至少两名独立标注者对该点的记录。", true);
        return;
      }
      adjudications[task.task_id] = row;
    }
    persist();
    setStatus("当前点已保存到浏览器草稿；请定期导出 CSV。", false);
    render();
  }

  function initializeSelectors() {
    boot.frames.forEach((frame) => {
      const option = document.createElement("option");
      option.value = String(frame.source_frame_index);
      option.textContent = `frame ${frame.source_frame_index} · ${frame.timestamp_ms}ms · ${frame.bbox_size_stratum} · ${frame.bbox_long_side_px.toFixed(1)}px`;
      $("#frame").appendChild(option);
    });
    boot.joint_names.forEach((joint) => {
      const option = document.createElement("option");
      option.value = joint; option.textContent = joint; $("#joint").appendChild(option);
    });
    video.src = boot.video_source;
  }
  function seek() {
    const frame = currentFrame();
    if (!frame) return;
    video.pause();
    video.currentTime = frame.timestamp_ms / 1000;
    $("#frame-meta").textContent = `bbox ${frame.bbox_width_px.toFixed(1)}×${frame.bbox_height_px.toFixed(1)}px · ${frame.bbox_size_stratum} · events ${frame.affected_event_ids.join(", ") || "—"}`;
    render();
  }
  function updateCrop() {
    const frame = currentFrame();
    if (!frame || video.readyState < 2) return false;
    const [x1, y1, x2, y2] = frame.bbox_px;
    const centerX = (x1 + x2) / 2, centerY = (y1 + y2) / 2;
    const side = Math.max(96, frame.bbox_long_side_px * 5.5);
    let sx = Math.max(0, centerX - side / 2), sy = Math.max(0, centerY - side / 2);
    let sw = Math.min(side, frame.width - sx), sh = Math.min(side, frame.height - sy);
    if (sw < side) sx = Math.max(0, frame.width - side);
    if (sh < side) sy = Math.max(0, frame.height - side);
    sw = Math.min(side, frame.width - sx); sh = Math.min(side, frame.height - sy);
    crop = { sx, sy, sw, sh, frameWidth: frame.width, frameHeight: frame.height };
    return true;
  }
  function drawPoint(row, color, label, radius = 7) {
    if (!row || String(row.visible).toLowerCase() !== "true" || !crop) return;
    const x = (Number(row.x_normalized) * crop.frameWidth - crop.sx) / crop.sw * canvas.width;
    const y = (Number(row.y_normalized) * crop.frameHeight - crop.sy) / crop.sh * canvas.height;
    context.beginPath(); context.arc(x, y, radius, 0, Math.PI * 2); context.fillStyle = color; context.fill();
    context.strokeStyle = "#111"; context.lineWidth = 2; context.stroke();
    context.fillStyle = color; context.font = "18px system-ui"; context.fillText(label, x + 10, y - 10);
  }
  function draw() {
    if (!updateCrop()) return;
    context.clearRect(0, 0, canvas.width, canvas.height);
    context.drawImage(video, crop.sx, crop.sy, crop.sw, crop.sh, 0, 0, canvas.width, canvas.height);
    const task = currentTask();
    if (!task) return;
    if (mode() === "annotate") {
      const row = identity() ? (sessions[identity()] || {})[task.task_id] : null;
      drawPoint(row, "#22d3ee", "你的人工点");
    } else {
      const colors = ["#fb7185", "#fbbf24", "#a78bfa", "#60a5fa"];
      sourceRows(task.task_id).forEach((row, index) => drawPoint(row, colors[index % colors.length], row.annotator_id, 6));
      drawPoint(adjudications[task.task_id], "#34d399", "最终裁决", 8);
    }
  }
  function render() {
    const task = currentTask();
    if (!task) return;
    $("#task-label").textContent = `${task.source_frame_index} / ${task.joint_name}`;
    $("#reason").value = "";
    const completed = identity() ? Object.keys(sessions[identity()] || {}).length : 0;
    const importedAnnotators = uniqueAnnotators(Array.from(imported.values())).size;
    $("#progress").textContent = mode() === "annotate"
      ? `当前标注者 ${completed}/${boot.tasks.length}；模型点未显示。`
      : `已导入 ${imported.size} 条、${importedAnnotators} 名标注者；已裁决 ${Object.keys(adjudications).length}/${boot.tasks.length}。`;
    draw();
  }
  function move(delta) {
    const task = currentTask();
    const index = Math.max(0, boot.tasks.findIndex((item) => item.task_id === task.task_id));
    const next = boot.tasks[(index + delta + boot.tasks.length) % boot.tasks.length];
    $("#frame").value = String(next.source_frame_index);
    $("#joint").value = next.joint_name;
    seek();
  }
  function clickCanvas(event) {
    const task = currentTask();
    if (!task || !crop) return;
    const bounds = canvas.getBoundingClientRect();
    const px = (event.clientX - bounds.left) / bounds.width * canvas.width;
    const py = (event.clientY - bounds.top) / bounds.height * canvas.height;
    const x = Math.max(0, Math.min(1, (crop.sx + px / canvas.width * crop.sw) / crop.frameWidth));
    const y = Math.max(0, Math.min(1, (crop.sy + py / canvas.height * crop.sh) / crop.frameHeight));
    const sources = sourceRows(task.task_id).map((row) => row.annotation_id).sort();
    saveRow(visibleRow(task, x, y, sources));
  }
  async function importFiles(files) {
    for (const file of files) {
      const parsed = parseCsv(await file.text());
      if (parsed.fields.join("|") === boot.annotation_fields.join("|")) {
        parsed.rows.forEach((row) => { if (row.annotation_id) imported.set(row.annotation_id, row); });
      } else if (parsed.fields.join("|") === boot.adjudication_fields.join("|")) {
        parsed.rows.forEach((row) => { if (row.task_id) adjudications[row.task_id] = row; });
      } else setStatus(`不支持的 CSV 表头：${file.name}`, true);
    }
    render();
  }

  initializeSelectors();
  $("#frame").addEventListener("change", seek);
  $("#joint").addEventListener("change", render);
  $("#mode").addEventListener("change", render);
  $("#identity").addEventListener("input", render);
  $("#previous").addEventListener("click", () => move(-1));
  $("#next").addEventListener("click", () => move(1));
  canvas.addEventListener("click", clickCanvas);
  video.addEventListener("seeked", draw);
  video.addEventListener("loadeddata", draw);
  $("#mark-invisible").addEventListener("click", () => {
    const task = currentTask(), reason = $("#reason").value.trim();
    if (!task || !reason) { setStatus("不可见点必须填写原因。", true); return; }
    const sources = sourceRows(task.task_id).map((row) => row.annotation_id).sort();
    saveRow(invisibleRow(task, reason, sources));
  });
  $("#clear").addEventListener("click", () => {
    const task = currentTask(); if (!task) return;
    if (mode() === "annotate" && identity()) delete session()[task.task_id];
    else delete adjudications[task.task_id];
    persist(); render();
  });
  $("#import").addEventListener("change", (event) => importFiles(Array.from(event.target.files || [])));
  $("#export-annotations").addEventListener("click", () => {
    if (!identity()) { setStatus("请先填写 annotator ID。", true); return; }
    const rows = Object.values(session()).sort((a, b) => a.task_id.localeCompare(b.task_id));
    download(`small-roi-keypoints-${sanitize(identity())}.csv`, toCsv(boot.annotation_fields, rows));
  });
  $("#export-adjudications").addEventListener("click", () => {
    if (!identity()) { setStatus("请先填写 reviewer ID。", true); return; }
    const rows = Object.values(adjudications).sort((a, b) => a.task_id.localeCompare(b.task_id));
    download("small-roi-keypoint-adjudications.csv", toCsv(boot.adjudication_fields, rows));
  });
  $("#reset").addEventListener("click", () => {
    if (!window.confirm("清空本浏览器中的全部小 ROI 标注与裁决草稿？")) return;
    sessions = {}; imported = new Map(); adjudications = {}; localStorage.removeItem(storageKey); render();
  });
  seek();
})();
