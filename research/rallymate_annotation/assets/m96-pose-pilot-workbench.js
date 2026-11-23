(function () {
  "use strict";

  const bootNode = document.getElementById("m96-pose-pilot-bootstrap");
  const boot = JSON.parse(bootNode.textContent);
  const $ = (selector) => document.querySelector(selector);
  const tasks = boot.tasks;
  const taskById = new Map(tasks.map((task) => [task.task_id, task]));
  const storageKey = `rallymate-m96:${boot.bundle_id}:${boot.role_slot}`;
  const video = $("#video");
  const canvas = $("#canvas");
  const context = canvas.getContext("2d");
  let identity = "";
  let rows = {};
  let activeVideoId = null;

  function show(message, error) {
    const node = $("#status");
    node.textContent = message;
    node.className = error ? "status error" : "status";
  }

  function cleanIdentity(value) {
    return value.trim();
  }

  function safe(value) {
    return value.replace(/[^A-Za-z0-9_.-]+/g, "-").slice(0, 80);
  }

  function csvCell(value) {
    const text = value === null || value === undefined ? "" : String(value);
    return /[",\r\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
  }

  function toCsv(fields, values) {
    const lines = [fields, ...values.map((row) => fields.map((field) => row[field] ?? ""))];
    return "\ufeff" + lines.map((line) => line.map(csvCell).join(",")).join("\r\n") + "\r\n";
  }

  function parseCsv(text) {
    const source = text.replace(/^\ufeff/, "");
    const parsed = [];
    let row = [], value = "", quoted = false;
    for (let index = 0; index < source.length; index += 1) {
      const char = source[index];
      if (quoted) {
        if (char === '"' && source[index + 1] === '"') { value += '"'; index += 1; }
        else if (char === '"') quoted = false;
        else value += char;
      } else if (char === '"') quoted = true;
      else if (char === ",") { row.push(value); value = ""; }
      else if (char === "\n") {
        row.push(value.replace(/\r$/, ""));
        if (row.some((cell) => cell !== "")) parsed.push(row);
        row = []; value = "";
      } else value += char;
    }
    if (quoted) throw new Error("CSV contains an unterminated quoted value");
    if (value || row.length) { row.push(value.replace(/\r$/, "")); parsed.push(row); }
    if (!parsed.length) throw new Error("CSV is empty");
    const fields = parsed[0];
    if (new Set(fields).size !== fields.length) throw new Error("CSV repeats a field");
    return {
      fields,
      rows: parsed.slice(1).map((values) => {
        if (values.length !== fields.length) throw new Error("CSV row width does not match its header");
        return Object.fromEntries(fields.map((field, index) => [field, values[index]]));
      }),
    };
  }

  function persist() {
    try {
      localStorage.setItem(storageKey, JSON.stringify({ version: "1.0.0", identity, rows }));
    } catch (_) {
      show("浏览器草稿保存失败，请立即导出 CSV。", true);
    }
  }

  function restoreLocal() {
    try {
      const saved = JSON.parse(localStorage.getItem(storageKey) || "null");
      if (saved && saved.version === "1.0.0" && typeof saved.rows === "object") {
        identity = typeof saved.identity === "string" ? saved.identity : "";
        rows = saved.rows;
      }
    } catch (_) {
      rows = {};
    }
  }

  function currentTask() {
    return taskById.get($("#task").value);
  }

  function rowIdentityField() {
    return boot.role_slot === "C" ? "reviewer_id" : "annotator_id";
  }

  function makeRow(task, visible, x, y, reason) {
    const now = new Date().toISOString();
    if (boot.role_slot === "C") {
      return {
        schema_version: boot.submission_schema_version,
        submission_version: boot.submission_version,
        adjudication_bundle_id: boot.bundle_id,
        task_contract_sha256: boot.task_contract_sha256,
        adjudication_id: `m96-adj-${safe(identity)}-${task.task_id}`,
        task_id: task.task_id,
        source_annotation_a_id: task.sources[0].annotation_id,
        source_annotation_b_id: task.sources[1].annotation_id,
        reviewer_id: identity,
        visible: visible ? "true" : "false",
        x_normalized: visible ? x.toFixed(8) : "",
        y_normalized: visible ? y.toFixed(8) : "",
        visibility_reason: visible ? "" : reason,
        adjudicated_at: now,
      };
    }
    return {
      schema_version: boot.submission_schema_version,
      submission_version: boot.submission_version,
      bundle_id: boot.bundle_id,
      role_slot: boot.role_slot,
      task_contract_sha256: boot.task_contract_sha256,
      annotation_id: `m96-ann-${boot.role_slot}-${safe(identity)}-${task.task_id}`,
      task_id: task.task_id,
      frame_id: task.frame_id,
      video_id: task.video_id,
      source_frame_index: String(task.source_frame_index),
      joint_index: String(task.joint_index),
      joint_name: task.joint_name,
      annotator_id: identity,
      visible: visible ? "true" : "false",
      x_normalized: visible ? x.toFixed(8) : "",
      y_normalized: visible ? y.toFixed(8) : "",
      visibility_reason: visible ? "" : reason,
      annotated_at: now,
    };
  }

  function validateIdentity() {
    identity = cleanIdentity($("#identity").value);
    if (!/^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/.test(identity)) {
      show("角色 ID 只能含字母、数字、点、下划线或连字符，最长 64 字符。", true);
      return false;
    }
    if (boot.role_slot === "C" && boot.excluded_reviewer_ids.some((value) => value.toLowerCase() === identity.toLowerCase())) {
      show("reviewer C 的角色 ID 不得与 annotator A 或 B 复用。", true);
      return false;
    }
    return true;
  }

  function saveRow(row) {
    if (!validateIdentity()) return;
    rows[row.task_id] = row;
    persist();
    const index = tasks.findIndex((task) => task.task_id === row.task_id);
    if (index >= 0 && index < tasks.length - 1) {
      $("#task").value = tasks[index + 1].task_id;
      $("#reason").value = "";
      seek();
    } else render();
    show(`已保存并前进：${Object.keys(rows).length}/${tasks.length}。`);
  }

  function drawPoint(row, color, label, radius) {
    if (!row || String(row.visible).toLowerCase() !== "true") return;
    const x = Number(row.x_normalized) * canvas.width;
    const y = Number(row.y_normalized) * canvas.height;
    if (!Number.isFinite(x) || !Number.isFinite(y)) return;
    context.beginPath(); context.arc(x, y, radius, 0, Math.PI * 2);
    context.fillStyle = color; context.fill();
    context.strokeStyle = "#07111f"; context.lineWidth = 2; context.stroke();
    context.fillStyle = color; context.font = "16px system-ui"; context.fillText(label, x + 10, y - 8);
  }

  function draw() {
    if (video.readyState < 2) return;
    context.clearRect(0, 0, canvas.width, canvas.height);
    context.drawImage(video, 0, 0, canvas.width, canvas.height);
    const task = currentTask();
    if (!task) return;
    if (boot.role_slot === "C") {
      drawPoint(task.sources[0], "#fb7185", "A", 6);
      drawPoint(task.sources[1], "#fbbf24", "B", 6);
    }
    drawPoint(rows[task.task_id], "#34d399", boot.role_slot === "C" ? "C" : "你的点", 8);
  }

  function seek() {
    const task = currentTask();
    if (!task) return;
    video.pause();
    const applyTime = () => { video.currentTime = task.timestamp_ms / 1000; };
    if (activeVideoId !== task.video_id) {
      activeVideoId = task.video_id;
      video.src = boot.videos[task.video_id];
      video.addEventListener("loadedmetadata", applyTime, { once: true });
    } else applyTime();
    render();
  }

  function sourceText(source) {
    if (source.visible === "true") return `可见；(${source.x_normalized}, ${source.y_normalized})`;
    return `不可见；原因：${source.visibility_reason}`;
  }

  function render() {
    const task = currentTask();
    if (!task) return;
    $("#identity").value = identity;
    $("#task-label").textContent = `${task.video_id.slice(0, 8)} · frame ${task.source_frame_index} · ${task.joint_index}: ${task.joint_name}`;
    $("#frame-meta").textContent = `${task.timestamp_ms} ms · ${task.frame_width}×${task.frame_height} · ${task.target_instruction}`;
    $("#progress").textContent = `${boot.role_slot} 角色草稿 ${Object.keys(rows).length}/${tasks.length}；模型点位未加载。`;
    const sourcePanel = $("#sources");
    if (boot.role_slot === "C") {
      sourcePanel.hidden = false;
      sourcePanel.innerHTML = "";
      task.sources.forEach((source) => {
        const node = document.createElement("div");
        node.className = `source ${source.slot.toLowerCase()}`;
        node.textContent = `${source.slot}: ${sourceText(source)} · ${source.annotation_id}`;
        sourcePanel.appendChild(node);
      });
    } else sourcePanel.hidden = true;
    draw();
  }

  function move(delta) {
    const current = currentTask();
    const index = Math.max(0, tasks.findIndex((task) => current && task.task_id === current.task_id));
    $("#task").value = tasks[(index + delta + tasks.length) % tasks.length].task_id;
    seek();
  }

  function clickCanvas(event) {
    const task = currentTask();
    if (!task || !validateIdentity()) return;
    const bounds = canvas.getBoundingClientRect();
    const x = Math.max(0, Math.min(0.99999999, (event.clientX - bounds.left) / bounds.width));
    const y = Math.max(0, Math.min(0.99999999, (event.clientY - bounds.top) / bounds.height));
    saveRow(makeRow(task, true, x, y, ""));
  }

  function download(name, content) {
    const link = document.createElement("a");
    link.href = URL.createObjectURL(new Blob([content], { type: "text/csv;charset=utf-8" }));
    link.download = name;
    document.body.appendChild(link); link.click(); link.remove();
    setTimeout(() => URL.revokeObjectURL(link.href), 5000);
  }

  async function importCsv(file) {
    try {
      const parsed = parseCsv(await file.text());
      if (parsed.fields.join("\u0000") !== boot.fields.join("\u0000")) throw new Error("CSV header does not match this role");
      const next = {};
      let importedIdentity = null;
      for (const row of parsed.rows) {
        const task = taskById.get(row.task_id);
        if (!task) throw new Error(`unknown task: ${row.task_id}`);
        if (boot.role_slot === "C") {
          if (row.adjudication_bundle_id !== boot.bundle_id || row.task_contract_sha256 !== boot.task_contract_sha256) throw new Error("CSV belongs to another C bundle");
        } else if (row.bundle_id !== boot.bundle_id || row.role_slot !== boot.role_slot || row.task_contract_sha256 !== boot.task_contract_sha256) {
          throw new Error("CSV belongs to another A/B role bundle");
        }
        const value = cleanIdentity(row[rowIdentityField()] || "");
        if (!importedIdentity) importedIdentity = value;
        if (!value || value.toLowerCase() !== importedIdentity.toLowerCase()) throw new Error("CSV mixes role IDs");
        if (next[row.task_id]) throw new Error(`duplicate task: ${row.task_id}`);
        next[row.task_id] = row;
      }
      identity = importedIdentity || identity;
      rows = next;
      $("#identity").value = identity;
      if (!validateIdentity()) throw new Error("CSV role ID is invalid for this package");
      persist(); render(); show(`已从 CSV 恢复 ${Object.keys(rows).length}/${tasks.length} 条草稿。`);
    } catch (error) {
      show(`CSV 导入失败：${error.message}`, true);
    }
  }

  restoreLocal();
  tasks.forEach((task, index) => {
    const option = document.createElement("option");
    option.value = task.task_id;
    option.textContent = `${index + 1}/${tasks.length} · ${task.video_id.slice(0, 8)} · f${task.source_frame_index} · ${task.joint_name}`;
    $("#task").appendChild(option);
  });
  $("#role").textContent = boot.role_slot;
  $("#task").addEventListener("change", seek);
  $("#previous").addEventListener("click", () => move(-1));
  $("#next").addEventListener("click", () => move(1));
  $("#identity").addEventListener("change", () => { identity = cleanIdentity($("#identity").value); persist(); render(); });
  canvas.addEventListener("click", clickCanvas);
  video.addEventListener("seeked", draw);
  video.addEventListener("loadeddata", draw);
  $("#mark-invisible").addEventListener("click", () => {
    const task = currentTask(), reason = $("#reason").value.trim();
    if (!task || !reason) { show("不可见点必须填写原因。", true); return; }
    saveRow(makeRow(task, false, 0, 0, reason));
  });
  $("#clear").addEventListener("click", () => {
    const task = currentTask(); if (!task) return;
    delete rows[task.task_id]; persist(); render();
  });
  $("#import").addEventListener("change", (event) => {
    const files = Array.from(event.target.files || []);
    if (files.length === 1) importCsv(files[0]);
    else show("每次请选择一份 CSV。", true);
    event.target.value = "";
  });
  $("#export").addEventListener("click", () => {
    if (!validateIdentity()) return;
    const ordered = tasks.filter((task) => rows[task.task_id]).map((task) => rows[task.task_id]);
    const name = boot.role_slot === "C" ? `m96-C-${safe(identity)}.csv` : `m96-${boot.role_slot}-${safe(identity)}.csv`;
    download(name, toCsv(boot.fields, ordered));
    show(`已导出 ${ordered.length}/${tasks.length} 条；只有完整 CSV 才能通过原子 intake。`, ordered.length !== tasks.length);
  });
  $("#reset").addEventListener("click", () => {
    if (!window.confirm(`清空此浏览器中的 M96 ${boot.role_slot} 角色草稿？`)) return;
    rows = {}; identity = ""; localStorage.removeItem(storageKey); render();
  });
  seek();
})();
