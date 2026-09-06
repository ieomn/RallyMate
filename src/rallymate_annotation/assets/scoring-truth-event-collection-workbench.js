(function () {
  "use strict";

  const Core = window.RallyMateScoringTruthEventCollectionCore;
  const byId = (id) => document.getElementById(id);
  const all = (selector) => [...document.querySelectorAll(selector)];
  let boot = null;
  let state = null;
  let stateStorageKey = "";
  let currentTaskId = "";
  let activeAnnotationId = "";
  let activeAdjudicationId = "";
  let pair = null;
  let mediaReady = false;
  let editorDirty = false;
  let reviewDirty = false;

  function compareCodepoints(left, right) {
    return left < right ? -1 : left > right ? 1 : 0;
  }

  function currentTask() {
    return boot && Core.taskById(boot, currentTaskId);
  }

  function currentVideoState() {
    return state && state.videos[currentTaskId];
  }

  function setStatus(message, kind) {
    const node = byId("workbench-status");
    node.textContent = message;
    node.className = `status${kind ? ` ${kind}` : ""}`;
  }

  function setMediaStatus(message, kind) {
    const node = byId("media-status");
    node.textContent = message;
    node.className = kind || "";
  }

  function failClosed(error) {
    document.body.dataset.workbenchState = "invalid";
    all("button,input,select,textarea").forEach((node) => { node.disabled = true; });
    setStatus(`工作台已关闭：${error && error.message ? error.message : String(error)}`, "error");
  }

  function reportError(error) {
    setStatus(error && error.message ? error.message : String(error), "error");
  }

  function integerValue(id, name) {
    const raw = byId(id).value.trim();
    if (!/^(0|[1-9]\d*)$/.test(raw)) throw new Error(`${name} 必须是非负整数`);
    const value = Number(raw);
    if (!Number.isSafeInteger(value)) throw new Error(`${name} 超出安全整数范围`);
    return value;
  }

  function setValue(id, value) {
    byId(id).value = value === null || value === undefined ? "" : String(value);
  }

  function markDirty() {
    editorDirty = true;
    updateControls();
  }

  function confirmDiscard() {
    if (!editorDirty && !reviewDirty) return true;
    const accepted = window.confirm("当前工作区有尚未保存的字段。放弃这些字段并继续吗？");
    if (accepted) {
      editorDirty = false;
      reviewDirty = false;
      if (state && boot.role_slot !== "C") renderReview();
      if (state && boot.role_slot === "C") renderCReview();
    }
    return accepted;
  }

  function persist() {
    if (!state || !stateStorageKey) return;
    localStorage.setItem(stateStorageKey, Core.canonicalJson(state));
  }

  function downloadCanonical(value, filename) {
    const blob = new Blob([Core.serializeCanonicalFile(value)], {type: "application/json;charset=utf-8"});
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 0);
  }

  function phaseCard(phaseKey, phase, capturePrefix) {
    const card = document.createElement("div");
    card.className = "phase-card";
    card.dataset.phaseKey = phaseKey;
    const heading = document.createElement("h3");
    heading.textContent = phaseKey;
    card.appendChild(heading);

    const statusLabel = document.createElement("label");
    statusLabel.append("status");
    const status = document.createElement("select");
    status.className = "phase-status";
    [["", "请选择"], ["observed", "observed"], ["unobservable", "unobservable"]].forEach(([value, label]) => {
      const option = document.createElement("option");
      option.value = value;
      option.textContent = label;
      status.appendChild(option);
    });
    status.value = phase ? phase.status : "";
    statusLabel.appendChild(status);
    card.appendChild(statusLabel);

    const timestampLabel = document.createElement("label");
    timestampLabel.append("timestamp_ms");
    const timestamp = document.createElement("input");
    timestamp.className = "phase-timestamp";
    timestamp.inputMode = "numeric";
    timestamp.value = phase && phase.timestamp_ms !== null ? String(phase.timestamp_ms) : "";
    timestampLabel.appendChild(timestamp);
    card.appendChild(timestampLabel);

    const capture = document.createElement("button");
    capture.type = "button";
    capture.textContent = "取当前时间";
    capture.dataset.phaseCapture = capturePrefix;
    capture.addEventListener("click", () => {
      if (!mediaReady) return;
      timestamp.value = String(Math.round(byId("workbench-video").currentTime * 1000));
      status.value = "observed";
      sync();
      markDirty();
    });
    card.appendChild(capture);

    const reasonLabel = document.createElement("label");
    reasonLabel.className = "phase-reason";
    reasonLabel.append("unobservable reason");
    const reason = document.createElement("input");
    reason.className = "phase-reason-input";
    reason.value = phase ? phase.reason : "";
    reasonLabel.appendChild(reason);
    card.appendChild(reasonLabel);

    function sync() {
      const observed = status.value === "observed";
      timestamp.disabled = !observed;
      capture.disabled = !observed || !mediaReady;
      reason.disabled = status.value !== "unobservable";
      if (observed) reason.value = "";
      if (status.value === "unobservable") timestamp.value = "";
    }
    status.addEventListener("change", () => { sync(); markDirty(); });
    timestamp.addEventListener("input", markDirty);
    reason.addEventListener("input", markDirty);
    sync();
    return card;
  }

  function renderPhases(containerId, eventCode, values, capturePrefix) {
    const container = byId(containerId);
    container.replaceChildren();
    if (!Core.EVENT_CODES.includes(eventCode)) return;
    Core.PHASE_KEYS_BY_EVENT[eventCode].forEach((phaseKey) => {
      container.appendChild(phaseCard(phaseKey, values && values[phaseKey], capturePrefix));
    });
  }

  function readPhases(containerId) {
    return Object.fromEntries([...byId(containerId).querySelectorAll(".phase-card")].map((card) => {
      const status = card.querySelector(".phase-status").value;
      const timestampRaw = card.querySelector(".phase-timestamp").value.trim();
      const timestamp = status === "observed" && /^(0|[1-9]\d*)$/.test(timestampRaw) ? Number(timestampRaw) : null;
      return [card.dataset.phaseKey, {
        status,
        timestamp_ms: timestamp,
        reason: card.querySelector(".phase-reason-input").value,
      }];
    }));
  }

  function readEvent(finalEvent) {
    const prefix = finalEvent ? "final-event" : "event";
    const idKey = finalEvent ? "event_id" : "annotation_id";
    return {
      [idKey]: byId(`${prefix}-id`).value.trim(),
      event_code: byId(`${prefix}-code`).value,
      start_ms: integerValue(`${prefix}-start`, `${prefix} start_ms`),
      end_ms: integerValue(`${prefix}-end`, `${prefix} end_ms`),
      phase_observations: readPhases(`${prefix}-phases`),
      confidence_milli: integerValue(`${prefix}-confidence`, `${prefix} confidence_milli`),
      boundary_uncertainty_ms: integerValue(`${prefix}-uncertainty`, `${prefix} boundary_uncertainty_ms`),
      notes: byId(`${prefix}-notes`).value,
    };
  }

  function loadEventFields(event, finalEvent) {
    const prefix = finalEvent ? "final-event" : "event";
    const idKey = finalEvent ? "event_id" : "annotation_id";
    setValue(`${prefix}-id`, event[idKey]);
    setValue(`${prefix}-code`, event.event_code);
    setValue(`${prefix}-start`, event.start_ms);
    setValue(`${prefix}-end`, event.end_ms);
    setValue(`${prefix}-confidence`, event.confidence_milli);
    setValue(`${prefix}-uncertainty`, event.boundary_uncertainty_ms);
    setValue(`${prefix}-notes`, event.notes);
    renderPhases(`${prefix}-phases`, event.event_code, event.phase_observations, prefix);
  }

  function nextId(kind, records) {
    const prefix = `m93:${boot.role_slot}:${currentTask().video_id}:${kind}-`;
    let number = 1;
    while (Object.hasOwn(records, `${prefix}${String(number).padStart(4, "0")}`)) number += 1;
    return `${prefix}${String(number).padStart(4, "0")}`;
  }

  function recordButton(parts, selected, stale, onClick) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `record-row${selected ? " selected" : ""}${stale ? " stale" : ""}`;
    parts.forEach((part, index) => {
      const span = document.createElement("span");
      span.textContent = String(part);
      if (index === parts.length - 1) span.className = "tag";
      button.appendChild(span);
    });
    button.addEventListener("click", onClick);
    return button;
  }

  function showAnnotationEditor(id, isNew) {
    if (!confirmDiscard()) return;
    activeAnnotationId = id;
    const draft = currentVideoState().events[id] || Core.emptyEventDecision(id);
    byId("event-editor-empty").hidden = true;
    byId("event-editor").hidden = false;
    byId("event-editor").disabled = false;
    loadEventFields(draft, false);
    editorDirty = Boolean(isNew);
    renderAnnotationList();
    updateControls();
  }

  function hideAnnotationEditor() {
    activeAnnotationId = "";
    byId("event-editor-empty").hidden = false;
    byId("event-editor").hidden = true;
    editorDirty = false;
  }

  function renderAnnotationList() {
    if (!state || boot.role_slot === "C") return;
    const videoState = currentVideoState();
    const events = Object.values(videoState.events).sort((a, b) => a.start_ms - b.start_ms || compareCodepoints(a.annotation_id, b.annotation_id));
    const list = byId("event-list");
    list.replaceChildren();
    events.forEach((event) => list.appendChild(recordButton(
      [event.annotation_id, event.event_code, `${event.start_ms}–${event.end_ms} ms`, event.confidence_milli, "draft"],
      event.annotation_id === activeAnnotationId,
      false,
      () => showAnnotationEditor(event.annotation_id, false),
    )));
    byId("event-progress").textContent = `${currentTask().video_id}: ${events.length} 个已保存事件`;
  }

  function renderReview() {
    if (!state || boot.role_slot === "C") return;
    const review = currentVideoState().full_video_review;
    byId("review-completed").checked = Boolean(review && review.completed);
    byId("review-notes").value = review ? review.notes : "";
    const progress = byId("review-progress");
    progress.replaceChildren();
    boot.tasks.forEach((task) => {
      const complete = Boolean(state.videos[task.task_id].full_video_review);
      const chip = document.createElement("div");
      chip.className = `review-chip${complete ? " complete" : ""}`;
      chip.textContent = `${task.video_id}: ${complete ? "full_review 已保存" : "未完成"}`;
      progress.appendChild(chip);
    });
  }

  async function saveAnnotation() {
    try {
      if (!mediaReady) throw new Error("媒体门禁尚未通过，不能保存事件");
      const decision = readEvent(false);
      const previous = currentVideoState().events[decision.annotation_id] || null;
      const saved = Core.saveEventDraft(previous, decision, boot, currentTask(), new Date().toISOString());
      currentVideoState().events[decision.annotation_id] = saved;
      if (!previous || previous.annotated_at !== saved.annotated_at) {
        currentVideoState().full_video_review = null;
        reviewDirty = false;
      }
      currentVideoState().selected_annotation_id = decision.annotation_id;
      state.last_submission = null;
      persist();
      editorDirty = false;
      renderAnnotationList();
      renderReview();
      updateControls();
      setStatus("事件草稿已保存到当前角色/身份隔离的本地工作区。", "good");
    } catch (error) { reportError(error); }
  }

  function deleteAnnotation() {
    if (!mediaReady || !activeAnnotationId || !Object.hasOwn(currentVideoState().events, activeAnnotationId)) return;
    if (!window.confirm(`删除 ${activeAnnotationId} 的本地草稿？`)) return;
    delete currentVideoState().events[activeAnnotationId];
    currentVideoState().full_video_review = null;
    reviewDirty = false;
    state.last_submission = null;
    hideAnnotationEditor();
    persist();
    renderAnnotationList();
    renderReview();
    updateControls();
  }

  function saveReview() {
    try {
      if (!mediaReady) throw new Error("媒体门禁尚未通过，不能保存 full_review");
      const review = {completed: byId("review-completed").checked, notes: byId("review-notes").value};
      currentVideoState().full_video_review = Core.saveReviewDraft(currentVideoState().full_video_review, review, new Date().toISOString());
      state.last_submission = null;
      reviewDirty = false;
      persist();
      renderReview();
      updateControls();
      setStatus("当前视频 full_review 已保存。", "good");
    } catch (error) { reportError(error); }
  }

  async function exportAnnotations() {
    try {
      if (!mediaReady) throw new Error("媒体门禁尚未通过，不能导出提交");
      const submission = await Core.buildAnnotationSubmission(boot, state, {previousSubmission: state.last_submission});
      state.last_submission = submission;
      persist();
      downloadCanonical(submission, `${submission.submission_id}.json`);
      setStatus(`已导出 ${submission.submission_id}；revision ${submission.submission_revision_sha256}`, "good");
    } catch (error) { reportError(error); }
  }

  async function refreshPair() {
    pair = null;
    if (!state || boot.role_slot !== "C" || !state.imports.A || !state.imports.B) return;
    pair = await Core.validateAnnotationPair(boot, [state.imports.A, state.imports.B], state.participant_id);
    state.adjudications = Core.markStaleAdjudications(state.adjudications, pair);
    state.video_adjudications = Core.markStaleVideoAdjudicationReviews(state.video_adjudications, boot, pair);
  }

  async function importSubmissions(event) {
    try {
      if (!confirmDiscard()) return;
      const files = [...event.target.files];
      if (!files.length || files.length > 2) throw new Error("请选择一份或两份 canonical submission JSON");
      const staged = {...state.imports};
      for (const file of files) {
        const bytes = new Uint8Array(await file.arrayBuffer());
        const rawSha = await Core.sha256Bytes(bytes);
        const submission = Core.parseCanonicalJsonBytes(bytes);
        await Core.validateAnnotationSubmission(boot, submission, {rawSha256: rawSha});
        staged[submission.role_slot] = {raw_sha256: rawSha, submission};
      }
      state.imports = staged;
      await refreshPair();
      state.last_adjudication_submission = null;
      persist();
      renderReviewer();
      byId("import-status").textContent = pair
        ? `A/B exact source submissions 已验证：${pair.A.submission_id} / ${pair.B.submission_id}`
        : "已验证一份；仍需导入另一固定角色的 exact submission。";
      setStatus("来源文件通过 canonical bytes、raw SHA、revision、身份与执行包全等校验。", "good");
    } catch (error) { reportError(error); }
    finally { event.target.value = ""; }
  }

  function eventsFor(slot) {
    if (!pair) return [];
    const video = pair[slot].videos.find((row) => row.video_id === currentTask().video_id);
    return video ? video.events : [];
  }

  function fillCompareSelect(slot) {
    const select = byId(`compare-${slot.toLowerCase()}`);
    const prior = select.value;
    select.replaceChildren();
    const empty = document.createElement("option");
    empty.value = "";
    empty.textContent = `${slot}：不选择`;
    select.appendChild(empty);
    eventsFor(slot).forEach((event) => {
      const option = document.createElement("option");
      option.value = event.annotation_id;
      option.textContent = `${event.annotation_id} · ${event.event_code} · ${event.start_ms}–${event.end_ms}`;
      select.appendChild(option);
    });
    if ([...select.options].some((option) => option.value === prior)) select.value = prior;
    select.disabled = !pair;
  }

  function renderComparison() {
    fillCompareSelect("A");
    fillCompareSelect("B");
    const left = eventsFor("A").find((event) => event.annotation_id === byId("compare-a").value) || null;
    const right = eventsFor("B").find((event) => event.annotation_id === byId("compare-b").value) || null;
    const grid = byId("comparison-grid");
    grid.replaceChildren();
    const rows = [{field: "field", A: "A", B: "B", header: true}, ...Core.diffEvents(left, right)];
    rows.forEach((row) => {
      const node = document.createElement("div");
      node.className = `comparison-row${row.header ? " header" : ""}${row.different ? " diff" : ""}`;
      [row.field, row.A, row.B].forEach((value) => {
        const cell = document.createElement("div");
        cell.textContent = value === null ? "null" : (typeof value === "string" ? value : Core.canonicalJson(value));
        node.appendChild(cell);
      });
      grid.appendChild(node);
    });
  }

  function sourceRefRows(draft) {
    const selected = new Map((draft && draft.source_annotation_revisions || []).map((ref) => [`${ref.role_slot}\u0000${ref.annotation_id}`, ref]));
    const container = byId("source-revision-list");
    container.replaceChildren();
    ["A", "B"].forEach((slot) => eventsFor(slot).forEach((event) => {
      const key = `${slot}\u0000${event.annotation_id}`;
      const prior = selected.get(key);
      const row = document.createElement("div");
      row.className = "source-ref";
      row.dataset.roleSlot = slot;
      row.dataset.annotationId = event.annotation_id;
      row.dataset.annotationRevisionSha256 = event.annotation_revision_sha256;
      const check = document.createElement("input");
      check.type = "checkbox";
      check.className = "source-ref-check";
      check.checked = Boolean(prior);
      const role = document.createElement("strong");
      role.textContent = slot;
      const code = document.createElement("code");
      code.textContent = `${event.annotation_id} @ ${event.annotation_revision_sha256}`;
      const relation = document.createElement("select");
      relation.className = "source-ref-relation";
      ["supports", "merge_source", "split_source", "rejected_source"].forEach((value) => {
        const option = document.createElement("option");
        option.value = value;
        option.textContent = value;
        relation.appendChild(option);
      });
      relation.value = prior ? prior.relation : "supports";
      [check, relation].forEach((node) => node.addEventListener("change", markDirty));
      row.append(check, role, code, relation);
      container.appendChild(row);
    }));
  }

  function readSourceRefs() {
    return [...byId("source-revision-list").querySelectorAll(".source-ref")]
      .filter((row) => row.querySelector(".source-ref-check").checked)
      .map((row) => ({
        role_slot: row.dataset.roleSlot,
        annotation_id: row.dataset.annotationId,
        annotation_revision_sha256: row.dataset.annotationRevisionSha256,
        relation: row.querySelector(".source-ref-relation").value,
      }));
  }

  function emptyFinalEvent(id) {
    return {
      event_id: id,
      event_code: "",
      start_ms: null,
      end_ms: null,
      phase_observations: {},
      confidence_milli: null,
      boundary_uncertainty_ms: null,
      notes: "",
    };
  }

  function showAdjudicationEditor(id, isNew) {
    if (!confirmDiscard()) return;
    activeAdjudicationId = id;
    const saved = state.adjudications[id] || null;
    byId("adjudication-editor-empty").hidden = true;
    byId("adjudication-editor").hidden = false;
    byId("adjudication-editor").disabled = false;
    setValue("adjudication-id", id);
    setValue("adjudication-status", saved && saved.decision_status);
    setValue("adjudication-reason", saved && saved.decision_reason);
    const finalId = saved && saved.event ? saved.event.event_id : `m93:C:${currentTask().video_id}:event-${id.split("-").pop()}`;
    loadEventFields(saved && saved.event ? saved.event : emptyFinalEvent(finalId), true);
    sourceRefRows(saved);
    syncAdjudicationStatus();
    editorDirty = Boolean(isNew);
    renderAdjudicationList();
    updateControls();
  }

  function hideAdjudicationEditor() {
    activeAdjudicationId = "";
    byId("adjudication-editor-empty").hidden = false;
    byId("adjudication-editor").hidden = true;
    editorDirty = false;
  }

  function syncAdjudicationStatus() {
    const status = byId("adjudication-status").value;
    byId("final-event-fields").hidden = status === "rejected_sources";
    const cAdded = status === "c_added_event";
    byId("source-revision-list").querySelectorAll(".source-ref-relation").forEach((node) => {
      if (status === "rejected_sources") node.value = "rejected_source";
      else if (node.value === "rejected_source") node.value = "supports";
    });
    byId("source-revision-list").querySelectorAll("input,select").forEach((node) => { node.disabled = cAdded; });
  }

  function renderAdjudicationList() {
    if (!state || boot.role_slot !== "C") return;
    const rows = Object.values(state.adjudications).filter((row) => row.video_id === currentTask().video_id)
      .sort((a, b) => compareCodepoints(a.adjudication_id, b.adjudication_id));
    const list = byId("adjudication-list");
    list.replaceChildren();
    rows.forEach((draft) => list.appendChild(recordButton(
      [draft.adjudication_id, draft.decision_status, draft.event ? draft.event.event_code : "—", draft.source_annotation_revisions.length, draft.stale ? "stale" : "bound"],
      draft.adjudication_id === activeAdjudicationId,
      draft.stale,
      () => showAdjudicationEditor(draft.adjudication_id, false),
    )));
  }

  function renderCoverage() {
    const node = byId("coverage-status");
    if (!pair) {
      node.textContent = "尚未形成已验证 A/B pair。";
      return;
    }
    const report = Core.coverageReport(state.adjudications, pair);
    node.textContent = report.complete
      ? "全部来源 revision 已覆盖，且没有 stale 裁决。"
      : `未覆盖 ${report.missing.length}；非法重复 ${report.invalid_duplicates.length}；stale ${report.stale_adjudication_ids.length}`;
  }

  function renderCReview() {
    if (!state || boot.role_slot !== "C") return;
    const review = state.video_adjudications[currentTaskId];
    byId("c-review-completed").checked = Boolean(review && review.completed);
    byId("c-review-notes").value = review ? review.notes : "";
    const progress = byId("c-review-progress");
    progress.replaceChildren();
    boot.tasks.forEach((task) => {
      const complete = Boolean(state.video_adjudications[task.task_id]);
      const chip = document.createElement("div");
      chip.className = `review-chip${complete ? " complete" : ""}`;
      chip.textContent = `${task.video_id}: ${complete ? "C full_review 已保存" : "未完成"}`;
      progress.appendChild(chip);
    });
  }

  function renderReviewer() {
    if (!state || boot.role_slot !== "C") return;
    renderComparison();
    renderAdjudicationList();
    renderCoverage();
    renderCReview();
    updateControls();
  }

  function saveCReview() {
    try {
      if (!mediaReady) throw new Error("媒体门禁尚未通过，不能保存 C full_review");
      if (!pair) throw new Error("必须先导入并验证 exact A/B submissions");
      const review = {completed: byId("c-review-completed").checked, notes: byId("c-review-notes").value};
      state.video_adjudications[currentTaskId] = Core.saveVideoAdjudicationReview(
        state.video_adjudications[currentTaskId], review, boot, currentTask(), pair, new Date().toISOString(),
      );
      state.last_adjudication_submission = null;
      reviewDirty = false;
      persist();
      renderCReview();
      updateControls();
      setStatus("当前视频 C full_review 已保存并绑定 A/B video revisions。", "good");
    } catch (error) { reportError(error); }
  }

  async function saveAdjudication() {
    try {
      if (!mediaReady) throw new Error("媒体门禁尚未通过，不能保存裁决");
      if (!pair) throw new Error("必须先导入并验证 exact A/B submissions");
      const status = byId("adjudication-status").value;
      const input = {
        adjudication_id: byId("adjudication-id").value.trim(),
        video_id: currentTask().video_id,
        decision_status: status,
        source_annotation_revisions: status === "c_added_event" ? [] : readSourceRefs(),
        event: status === "rejected_sources" ? null : readEvent(true),
        decision_reason: byId("adjudication-reason").value,
      };
      const previous = state.adjudications[input.adjudication_id] || null;
      const saved = Core.saveAdjudicationDraft(previous, input, boot, pair, new Date().toISOString());
      state.adjudications[input.adjudication_id] = saved;
      if (!previous || previous.adjudicated_at !== saved.adjudicated_at) {
        state.video_adjudications[currentTaskId] = null;
        reviewDirty = false;
      }
      state.last_adjudication_submission = null;
      persist();
      editorDirty = false;
      renderReviewer();
      setStatus("裁决已保存，并绑定到当前 A/B video 与 annotation revisions。", "good");
    } catch (error) { reportError(error); }
  }

  function deleteAdjudication() {
    if (!mediaReady || !activeAdjudicationId || !Object.hasOwn(state.adjudications, activeAdjudicationId)) return;
    if (!window.confirm(`删除 ${activeAdjudicationId} 的本地裁决草稿？`)) return;
    delete state.adjudications[activeAdjudicationId];
    state.video_adjudications[currentTaskId] = null;
    reviewDirty = false;
    state.last_adjudication_submission = null;
    hideAdjudicationEditor();
    persist();
    renderReviewer();
  }

  async function exportAdjudications() {
    try {
      if (!mediaReady) throw new Error("媒体门禁尚未通过，不能导出裁决提交");
      const submission = await Core.buildAdjudicationSubmission(boot, state, {previousSubmission: state.last_adjudication_submission});
      state.last_adjudication_submission = submission;
      persist();
      downloadCanonical(submission, `m93-C-${boot.execution_id}-adjudication.json`);
      setStatus(`C submission 已导出；revision ${submission.adjudication_submission_revision_sha256}`, "good");
    } catch (error) { reportError(error); }
  }

  function updateMediaReady() {
    const video = byId("workbench-video");
    const task = currentTask();
    mediaReady = false;
    if (!task || !Number.isFinite(video.duration) || video.duration <= 0 || video.error) {
      setMediaStatus(video.error ? "媒体加载错误；所有写入按钮保持关闭。" : "等待可信媒体 metadata 与 seekable 范围。", video.error ? "error" : "");
      updateControls();
      return;
    }
    const actualMs = Math.round(video.duration * 1000);
    const frameMs = 1000 * task.frame_rate.denominator / task.frame_rate.numerator;
    const toleranceMs = Math.max(2, Math.ceil(frameMs + 10));
    const durationMatches = Math.abs(actualMs - task.duration_ms) <= toleranceMs;
    let fullSeekable = false;
    for (let index = 0; index < video.seekable.length; index += 1) {
      if (video.seekable.start(index) <= 0.1 && video.seekable.end(index) >= video.duration - 0.1) fullSeekable = true;
    }
    mediaReady = durationMatches && fullSeekable;
    setMediaStatus(
      mediaReady
        ? `mediaReady：浏览器时长 ${actualMs} ms 与绑定时长 ${task.duration_ms} ms 一致，且全片可 seek。`
        : `媒体门禁未通过：duration ${actualMs}/${task.duration_ms} ms；full seekable=${fullSeekable}。`,
      mediaReady ? "good" : "error",
    );
    updateControls();
    all("[data-phase-capture]").forEach((node) => { node.disabled = !mediaReady || node.parentElement.querySelector(".phase-status").value !== "observed"; });
  }

  function updateMediaTime() {
    const video = byId("workbench-video");
    const current = Number.isFinite(video.currentTime) ? Math.round(video.currentTime * 1000) : 0;
    byId("media-time").textContent = `${current} ms / ${currentTask() ? `${currentTask().duration_ms} ms` : "—"}`;
    byId("seek-slider").value = Number.isFinite(video.currentTime) ? String(video.currentTime) : "0";
  }

  function loadTask(taskId) {
    currentTaskId = taskId;
    activeAnnotationId = "";
    activeAdjudicationId = "";
    editorDirty = false;
    reviewDirty = false;
    mediaReady = false;
    const task = currentTask();
    byId("task-select").value = taskId;
    const video = byId("workbench-video");
    video.src = task.media_path;
    video.load();
    byId("seek-slider").max = String(task.duration_ms / 1000);
    byId("media-fps").textContent = `${task.frame_rate.numerator}/${task.frame_rate.denominator} fps`;
    setMediaStatus("正在校验浏览器 metadata、绑定时长与全片 seekable 范围。", "");
    if (boot.role_slot === "C") {
      hideAdjudicationEditor();
      renderReviewer();
    } else {
      hideAnnotationEditor();
      renderAnnotationList();
      renderReview();
    }
    updateControls();
  }

  function switchTask() {
    const prior = currentTaskId;
    const next = byId("task-select").value;
    if (next === prior) return;
    if (!confirmDiscard()) {
      byId("task-select").value = prior;
      return;
    }
    loadTask(next);
  }

  function updateControls() {
    const active = Boolean(state);
    byId("task-select").disabled = !active;
    byId("play-pause").disabled = !active;
    all("[data-step-frames],[data-step-ms]").forEach((node) => { node.disabled = !active; });
    byId("seek-slider").disabled = !active;
    byId("reset-session").disabled = !stateStorageKey;
    if (!boot) return;
    if (boot.role_slot === "C") {
      byId("import-submissions").disabled = !active;
      byId("add-adjudication").disabled = !active || !pair || !mediaReady;
      byId("save-adjudication").disabled = !active || !pair || !activeAdjudicationId || !mediaReady;
      byId("delete-adjudication").disabled = !active || !activeAdjudicationId || !mediaReady;
      let complete = false;
      if (pair) complete = Core.coverageReport(state.adjudications, pair).complete;
      const reviewsComplete = active && boot.tasks.every((task) => Boolean(state.video_adjudications[task.task_id]));
      byId("c-review-completed").disabled = !active || !pair || !mediaReady;
      byId("c-review-notes").disabled = !active || !pair || !mediaReady;
      byId("save-c-review").disabled = !active || !pair || !mediaReady;
      byId("export-adjudications").disabled = !active || !pair || !complete || !reviewsComplete || !mediaReady || editorDirty || reviewDirty;
    } else {
      byId("add-event").disabled = !active || !mediaReady;
      byId("save-event").disabled = !active || !activeAnnotationId || !mediaReady;
      byId("delete-event").disabled = !active || !activeAnnotationId || !Object.hasOwn(currentVideoState().events, activeAnnotationId) || !mediaReady;
      byId("review-completed").disabled = !active || !mediaReady;
      byId("review-notes").disabled = !active || !mediaReady;
      byId("save-review").disabled = !active || !mediaReady;
      const reviewsComplete = active && boot.tasks.every((task) => Boolean(state.videos[task.task_id].full_video_review));
      byId("export-annotations").disabled = !reviewsComplete || !mediaReady || editorDirty || reviewDirty;
    }
  }

  async function startSession() {
    try {
      const participantId = byId("participant-id").value.trim();
      Core.normalizeIdentity(participantId);
      stateStorageKey = await Core.storageKey(boot, boot.role_slot, participantId);
      const stored = localStorage.getItem(stateStorageKey);
      if (stored) {
        state = JSON.parse(stored);
        if (Core.canonicalJson(state) !== stored) throw new Error("本地草稿不是 canonical JSON，已拒绝载入；可清空该身份草稿后重试");
      } else state = Core.createState(boot, participantId);
      Core.validateRestoredState(boot, participantId, state);
      if (boot.role_slot === "C") await refreshPair();
      byId("participant-id").disabled = true;
      byId("start-session").disabled = true;
      byId(boot.role_slot === "C" ? "reviewer-workspace" : "annotator-workspace").hidden = false;
      document.body.dataset.workbenchState = "active";
      loadTask(boot.tasks[0].task_id);
      persist();
      setStatus(`固定角色 ${boot.role_slot} 已进入身份隔离工作区；草稿持续保存在本机。`, "good");
    } catch (error) {
      state = null;
      reportError(error);
      updateControls();
    }
  }

  function resetSession() {
    if (!stateStorageKey || !window.confirm("永久清空当前 bundle / binding / role / participant 的本地草稿？")) return;
    localStorage.removeItem(stateStorageKey);
    window.location.reload();
  }

  function bindEvents() {
    byId("start-session").addEventListener("click", startSession);
    byId("reset-session").addEventListener("click", resetSession);
    byId("task-select").addEventListener("change", switchTask);
    byId("play-pause").addEventListener("click", () => {
      const video = byId("workbench-video");
      if (video.paused) video.play().catch(reportError); else video.pause();
    });
    all("[data-step-frames]").forEach((button) => button.addEventListener("click", () => {
      const task = currentTask();
      const delta = Number(button.dataset.stepFrames) * task.frame_rate.denominator / task.frame_rate.numerator;
      byId("workbench-video").currentTime = Math.max(0, Math.min(task.duration_ms / 1000, byId("workbench-video").currentTime + delta));
    }));
    all("[data-step-ms]").forEach((button) => button.addEventListener("click", () => {
      const task = currentTask();
      byId("workbench-video").currentTime = Math.max(0, Math.min(task.duration_ms / 1000, byId("workbench-video").currentTime + Number(button.dataset.stepMs) / 1000));
    }));
    byId("seek-slider").addEventListener("input", () => { byId("workbench-video").currentTime = Number(byId("seek-slider").value); });
    ["loadedmetadata", "durationchange", "progress", "canplay"].forEach((name) => byId("workbench-video").addEventListener(name, updateMediaReady));
    byId("workbench-video").addEventListener("timeupdate", updateMediaTime);
    byId("workbench-video").addEventListener("error", updateMediaReady);
    all("[data-capture]").forEach((button) => button.addEventListener("click", () => {
      if (!mediaReady) return;
      setValue(button.dataset.capture, Math.round(byId("workbench-video").currentTime * 1000));
      markDirty();
    }));
    byId("add-event").addEventListener("click", () => showAnnotationEditor(nextId("event", currentVideoState().events), true));
    byId("event-code").addEventListener("change", () => { renderPhases("event-phases", byId("event-code").value, null, "event"); markDirty(); });
    byId("save-event").addEventListener("click", saveAnnotation);
    byId("delete-event").addEventListener("click", deleteAnnotation);
    byId("save-review").addEventListener("click", saveReview);
    byId("review-completed").addEventListener("change", () => { reviewDirty = true; updateControls(); });
    byId("review-notes").addEventListener("input", () => { reviewDirty = true; updateControls(); });
    byId("export-annotations").addEventListener("click", exportAnnotations);
    byId("import-submissions").addEventListener("change", importSubmissions);
    byId("compare-a").addEventListener("change", renderComparison);
    byId("compare-b").addEventListener("change", renderComparison);
    byId("add-adjudication").addEventListener("click", () => showAdjudicationEditor(nextId("adjudication", state.adjudications), true));
    byId("adjudication-status").addEventListener("change", () => { syncAdjudicationStatus(); markDirty(); });
    byId("final-event-code").addEventListener("change", () => { renderPhases("final-event-phases", byId("final-event-code").value, null, "final-event"); markDirty(); });
    byId("save-adjudication").addEventListener("click", saveAdjudication);
    byId("delete-adjudication").addEventListener("click", deleteAdjudication);
    byId("export-adjudications").addEventListener("click", exportAdjudications);
    byId("save-c-review").addEventListener("click", saveCReview);
    byId("c-review-completed").addEventListener("change", () => { reviewDirty = true; updateControls(); });
    byId("c-review-notes").addEventListener("input", () => { reviewDirty = true; updateControls(); });
    [byId("event-editor"), byId("adjudication-editor")].forEach((editor) => {
      editor.addEventListener("input", (event) => { if (!event.target.readOnly) markDirty(); });
    });
    window.addEventListener("beforeunload", (event) => {
      if (!editorDirty && !reviewDirty) return;
      event.preventDefault();
      event.returnValue = "";
    });
  }

  function initialize() {
    if (!Core) throw new Error("核心合同模块未加载");
    const node = byId("scoring-truth-event-collection-bootstrap");
    boot = JSON.parse(node.textContent);
    Core.validateBootstrap(boot);
    const currentBundle = boot.role_slot === "C" ? boot.adjudication_bundle : boot.execution_bundle;
    byId("role-slot").textContent = boot.role_slot;
    byId("bundle-id").textContent = currentBundle.bundle_id;
    byId("binding-sha").textContent = boot.authorization_binding_sha256;
    boot.tasks.forEach((task) => {
      const option = document.createElement("option");
      option.value = task.task_id;
      option.textContent = `${task.video_id} · ${task.duration_ms} ms`;
      byId("task-select").appendChild(option);
    });
    bindEvents();
    document.body.dataset.workbenchState = "ready";
    setStatus(`发布合同已验证；本入口固定为角色 ${boot.role_slot}。请输入 participant ID。`, "good");
    window.RallyMateScoringTruthEventCollectionWorkbench = Object.freeze({
      getSnapshot: () => Object.freeze({
        role_slot: boot.role_slot,
        execution_id: boot.execution_id,
        session_started: Boolean(state),
        mediaReady,
        editorDirty,
        reviewDirty,
        current_task_id: currentTaskId,
      }),
    });
  }

  try { initialize(); } catch (error) { failClosed(error); }
})();
