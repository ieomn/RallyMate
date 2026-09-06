(() => {
  "use strict";

  const form = document.querySelector("#upload-form");
  const fileInput = document.querySelector("#video-input");
  const dropZone = document.querySelector("#drop-zone");
  const fileTitle = document.querySelector("#file-title");
  const fileMeta = document.querySelector("#file-meta");
  const submitButton = document.querySelector("#submit-button");
  const resumeButton = document.querySelector("#resume-button");
  const recoveryNote = document.querySelector("#recovery-note");
  const stateText = document.querySelector("#state-text");
  const emptyState = document.querySelector("#empty-state");
  const progressState = document.querySelector("#progress-state");
  const resultState = document.querySelector("#result-state");
  const errorState = document.querySelector("#error-state");
  const errorMessage = document.querySelector("#error-message");
  const retryButton = document.querySelector("#retry-button");
  const progress = document.querySelector("#progress");
  const progressLabel = document.querySelector("#progress-label");
  const progressPercent = document.querySelector("#progress-percent");
  const runtimeBadge = document.querySelector("#runtime-badge");
  const scoreRing = document.querySelector("#score-ring");
  const scoreValue = document.querySelector("#score-value");
  const scoreUnit = document.querySelector("#score-unit");
  const scoreLabel = document.querySelector("#score-label");
  const resultHeadline = document.querySelector("#result-headline");
  const scoreMeaning = document.querySelector("#score-meaning");
  const trainingInsights = document.querySelector("#training-insights");
  const strengthsPanel = document.querySelector("#strengths-panel");
  const strengthsList = document.querySelector("#strengths-list");
  const prioritiesPanel = document.querySelector("#priorities-panel");
  const prioritiesList = document.querySelector("#priorities-list");
  const analysisQualityValue = document.querySelector("#analysis-quality-value");
  const analysisQualityMeaning = document.querySelector("#analysis-quality-meaning");
  const trainingNote = document.querySelector("#training-note");
  const actionCards = document.querySelector("#action-cards");
  const resultLinks = document.querySelector("#result-links");
  const batchResultsSection = document.querySelector("#batch-results-section");
  const batchResultsCount = document.querySelector("#batch-results-count");
  const batchResults = document.querySelector("#batch-results");

  const phaseOrder = ["queued", "inference", "scoring_readiness", "done"];
  const storageKey = "rallymate-demo-batch-v1";
  const batchStateVersion = "1.0.0";
  const jobIdPattern = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
  const supportedActionCodes = new Set(["FS01", "FS02", "FS09"]);
  const supportedIndicatorIds = new Set([
    "FS01-M02", "FS01-M03", "FS01-M04", "FS01-M05",
    "FS02-M02", "FS02-M03", "FS02-M04", "FS02-M05",
    "FS09-M01", "FS09-M02", "FS09-M03", "FS09-M04", "FS09-M05",
  ]);
  const publicErrorMessagesByCode = Object.freeze({
    legacy_job_requires_reanalysis: "这个旧任务缺少新版评价数据，请重新选择原视频并分析。",
    invalid_api_key: "本地访问口令不正确，请检查后重试。",
    unauthorized: "需要正确的本地访问口令才能继续。",
    job_not_found: "没有找到这个分析任务，请重新提交视频。",
    job_not_succeeded: "这个任务还没有生成可用结果，请稍后重试。",
    unsupported_video_extension: "暂不支持这种视频格式，请改用 MP4、MOV、M4V、AVI 或 MKV。",
    video_too_large: "视频文件超过大小限制，请压缩或截取后重试。",
    video_too_long: "视频时长超过限制，请截取需要分析的片段后重试。",
    uploaded_video_empty: "所选视频没有可读取的内容，请重新选择文件。",
    video_decode_failed: "无法读取这个视频，请尝试转为 MP4 后重试。",
    demo_result_unavailable: "结果暂时无法整理，请稍后重试或重新分析视频。",
    analysis_failed: "这次视频分析没有完成，请重试；若仍失败，可换一段更短的视频。",
    service_not_ready: "本地分析服务还未准备好，请稍后重试。",
    rate_limited: "提交得太频繁，请稍等片刻再试。",
  });
  const publicErrorMessagesByStatus = Object.freeze({
    400: "提交内容无法处理，请检查分析设置后重试。",
    401: "需要正确的本地访问口令才能继续。",
    403: "当前没有执行此操作的权限。",
    404: "没有找到这个分析任务，请重新提交视频。",
    409: "任务当前还不能生成结果，请稍后重试。",
    413: "视频大小或时长超过限制，请压缩或截取后重试。",
    415: "暂不支持这种视频格式，请改用 MP4、MOV、M4V、AVI 或 MKV。",
    422: "无法读取这个视频或分析设置，请检查文件后重试。",
    429: "提交得太频繁，请稍等片刻再试。",
    500: "本地分析服务遇到问题，请稍后重试。",
    502: "本地分析服务返回异常，请稍后重试。",
    503: "本地分析服务还未准备好，请稍后重试。",
    504: "本次分析等待时间过长，请稍后重试。",
  });
  let activeJob = null;
  let restoreInProgress = false;

  const emptyBatchState = (total = 0) => ({
    version: batchStateVersion,
    submitted_job_ids: [],
    completed_job_ids: [],
    failed_job_ids: [],
    active_job_id: null,
    current_index: 0,
    total,
  });

  const uniqueJobIds = (value) => {
    if (!Array.isArray(value)) return [];
    return [...new Set(value.filter((item) => typeof item === "string" && jobIdPattern.test(item)))];
  };

  const sanitizeBatchState = (value) => {
    if (!value || typeof value !== "object" || value.version !== batchStateVersion) {
      return emptyBatchState();
    }
    const submitted = uniqueJobIds(value.submitted_job_ids);
    const completed = uniqueJobIds(value.completed_job_ids).filter((id) => submitted.includes(id));
    const failed = uniqueJobIds(value.failed_job_ids).filter((id) => submitted.includes(id));
    const active = typeof value.active_job_id === "string" && jobIdPattern.test(value.active_job_id)
      ? value.active_job_id
      : null;
    const total = Number.isInteger(value.total) && value.total >= 0 ? Math.min(value.total, 100) : 0;
    const currentIndex = Number.isInteger(value.current_index) && value.current_index >= 0
      ? Math.min(value.current_index, total)
      : 0;
    return {
      version: batchStateVersion,
      submitted_job_ids: submitted,
      completed_job_ids: completed,
      failed_job_ids: failed,
      active_job_id: active && submitted.includes(active) ? active : null,
      current_index: currentIndex,
      total,
    };
  };

  const loadBatchState = () => {
    try {
      const raw = window.localStorage.getItem(storageKey);
      return raw ? sanitizeBatchState(JSON.parse(raw)) : emptyBatchState();
    } catch (_) {
      return emptyBatchState();
    }
  };

  let savedBatch = loadBatchState();

  const persistBatchState = (value) => {
    savedBatch = sanitizeBatchState(value);
    try {
      // Deliberately persist only opaque job IDs and non-sensitive batch counters.
      window.localStorage.setItem(storageKey, JSON.stringify({
        version: savedBatch.version,
        submitted_job_ids: savedBatch.submitted_job_ids,
        completed_job_ids: savedBatch.completed_job_ids,
        failed_job_ids: savedBatch.failed_job_ids,
        active_job_id: savedBatch.active_job_id,
        current_index: savedBatch.current_index,
        total: savedBatch.total,
      }));
    } catch (_) {
      // The current page still works when browser storage is unavailable.
    }
  };

  const clearBatchState = () => {
    savedBatch = emptyBatchState();
    try {
      window.localStorage.removeItem(storageKey);
    } catch (_) {
      // Nothing else is required when browser storage is unavailable.
    }
  };

  const headers = () => {
    const token = document.querySelector("#token").value.trim();
    return token ? { Authorization: `Bearer ${token}` } : {};
  };

  const sleep = (milliseconds) =>
    new Promise((resolve) => window.setTimeout(resolve, milliseconds));

  const showOnly = (target) => {
    [emptyState, progressState, resultState, errorState].forEach((element) => {
      element.hidden = element !== target;
    });
  };

  const humanBytes = (bytes) => {
    if (!Number.isFinite(bytes) || bytes < 0) return "";
    if (bytes >= 1024 ** 3) return `${(bytes / 1024 ** 3).toFixed(2)} GB`;
    if (bytes >= 1024 ** 2) return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
    return `${Math.ceil(bytes / 1024)} KB`;
  };

  const batchPrefix = (context) =>
    context && context.total > 1 ? `第 ${context.index}/${context.total} 个 · ` : "";

  const updateFileLabel = () => {
    const files = [...(fileInput.files || [])];
    if (!files.length) {
      fileTitle.textContent = "点击选择，或把视频拖到这里";
      fileMeta.textContent = "建议使用固定机位、能看到完整身体的视频";
      submitButton.textContent = "开始分析";
      return;
    }
    const totalBytes = files.reduce((sum, file) => sum + file.size, 0);
    fileTitle.textContent = files.length === 1 ? files[0].name : `${files.length} 个视频已选择`;
    fileMeta.textContent = `${humanBytes(totalBytes)} · 将按顺序逐个分析`;
    submitButton.textContent = files.length === 1 ? "开始分析" : `依次分析 ${files.length} 个视频`;
  };

  const setProgress = (job, context) => {
    const percent = Math.max(0, Math.min(100, Number(job.progress?.percent) || 0));
    const phase = job.progress?.phase || job.status || "queued";
    const prefix = batchPrefix(context);
    const phaseMessages = {
      queued: "任务已提交，正在等待处理",
      inference: "正在识别球员与动作",
      scoring_readiness: "正在整理动作表现与训练建议",
      done: "分析完成",
      succeeded: "分析完成",
      failed: "分析未完成",
    };
    progress.value = percent;
    progressPercent.textContent = `${Math.round(percent)}%`;
    progressLabel.textContent = `${prefix}${phaseMessages[phase] || phaseMessages[job.status] || "正在分析视频"}`;
    stateText.textContent = `${prefix}${job.status === "queued" ? "等待处理" : "分析中"} · ${Math.round(percent)}%`;

    let currentIndex = phaseOrder.indexOf(phase);
    if (job.status === "succeeded") currentIndex = phaseOrder.length - 1;
    if (currentIndex < 0 && job.status === "running") currentIndex = 1;
    document.querySelectorAll(".phase-list li").forEach((item, index) => {
      item.classList.toggle("done", index < currentIndex);
      item.classList.toggle("active", index === currentIndex);
    });
  };

  const createText = (tag, className, text) => {
    const element = document.createElement(tag);
    if (className) element.className = className;
    element.textContent = text;
    return element;
  };

  const asObject = (value) => value && typeof value === "object" && !Array.isArray(value)
    ? value
    : {};

  const scoreOrNull = (value) => {
    if (value === null || value === undefined || value === "") return null;
    const number = Number(value);
    if (!Number.isFinite(number)) return null;
    return Math.max(0, Math.min(100, Math.round(number)));
  };

  const zhText = (source, keys, fallback = "") => {
    const object = asObject(source);
    for (const key of keys) {
      const value = object[key];
      if (typeof value === "string" && value.trim()) return value.trim();
      if (Array.isArray(value)) {
        const items = value.filter((item) => typeof item === "string" && item.trim());
        if (items.length) return items.join("；");
      }
    }
    return fallback;
  };

  const zhList = (value) => Array.isArray(value)
    ? value.filter((item) => typeof item === "string" && item.trim()).map((item) => item.trim())
    : [];

  const indicatorId = (evaluation) => {
    const value = asObject(evaluation).indicator_id;
    return typeof value === "string" ? value.toUpperCase() : "";
  };

  const allowedIndicatorEvaluations = (items, eventCode = null) => {
    if (!Array.isArray(items)) return [];
    const seen = new Set();
    return items.filter((item) => {
      const id = indicatorId(item);
      if (!supportedIndicatorIds.has(id) || seen.has(id)) return false;
      if (eventCode && !id.startsWith(`${eventCode}-`)) return false;
      seen.add(id);
      return true;
    });
  };

  const appendEvaluationDetail = (parent, label, value) => {
    if (!value) return;
    const row = document.createElement("p");
    row.className = "indicator-detail";
    row.append(createText("strong", "", label), document.createTextNode(value));
    parent.append(row);
  };

  const renderIndicatorEvaluation = (evaluation) => {
    const item = asObject(evaluation);
    const id = indicatorId(item);
    const score = scoreOrNull(item.score_0_to_100);
    const available = item.available !== false && score !== null;
    const card = document.createElement("article");
    card.className = `indicator-evaluation${available ? "" : " is-unavailable"}`;

    const heading = document.createElement("div");
    heading.className = "indicator-heading";
    const label = zhText(
      item,
      ["label_zh", "name_zh", "indicator_name_zh"],
      `动作单项 ${id}`,
    );
    heading.append(
      createText("div", "", label),
      createText("span", "indicator-score", available ? `${score} / 100` : "暂无法评价"),
    );
    card.append(heading);

    if (available) {
      const level = zhText(item, ["level_zh", "rating_zh"]);
      if (level) card.append(createText("p", "indicator-level", level));
    }
    const summary = zhText(
      item,
      ["summary_zh", "assessment_zh"],
      available ? "已根据当前视频形成单项参考判断。" : "当前视频证据不足，暂时无法形成可靠的单项判断。",
    );
    appendEvaluationDetail(card, "表现判断：", summary);
    appendEvaluationDetail(
      card,
      "视频证据：",
      zhText(item, ["evidence_zh", "evidence_summary_zh", "observation_zh", "basis_zh"]),
    );
    appendEvaluationDetail(
      card,
      "训练建议：",
      zhText(item, ["suggestion_zh", "training_advice_zh", "advice_zh", "priority_zh", "recommendation_zh"]),
    );
    const measuredCount = Number.isInteger(item.measured_instance_count) && item.measured_instance_count >= 0
      ? item.measured_instance_count
      : null;
    const totalCount = Number.isInteger(item.total_instance_count) && item.total_instance_count >= 0
      ? item.total_instance_count
      : null;
    if (measuredCount !== null && totalCount !== null) {
      card.append(createText("p", "indicator-sample-count", `${measuredCount}/${totalCount} 个片段形成可用测量`));
    }
    const limitations = zhList(item.limitations_zh);
    if (limitations.length) {
      appendEvaluationDetail(card, "评价说明：", limitations.join("；"));
    }
    return card;
  };

  const renderAction = (action, globalEvaluations) => {
    const article = document.createElement("article");
    article.className = "action-card";
    // action.formation_assessment remains diagnostic context and is never used as performance;
    // action.summary_zh is only a safe legacy narrative fallback.
    const assessment = asObject(action.performance_assessment);
    const actionScore = scoreOrNull(assessment.score_0_to_100);
    const actionAvailable = assessment.available !== false && actionScore !== null;
    const header = document.createElement("header");
    const titleWrap = document.createElement("div");
    titleWrap.append(
      createText("span", "action-code", action.event_code),
      createText("h4", "", action.name_zh || "动作表现"),
    );
    const statusWrap = document.createElement("div");
    statusWrap.className = "action-status-wrap";
    statusWrap.append(
      createText(
        "span",
        `performance-badge${actionAvailable ? "" : " is-unavailable"}`,
        actionAvailable
          ? zhText(assessment, ["level_zh", "label_zh"], "已形成参考评价")
          : "暂无法评价",
      ),
      createText("span", "performance-score", actionAvailable ? `${actionScore}/100` : "—"),
    );
    header.append(titleWrap, statusWrap);
    const detectedSegments = Number.isInteger(action.detected_segments) && action.detected_segments >= 0
      ? action.detected_segments
      : null;
    article.append(
      header,
      createText(
        "p",
        "action-summary",
        zhText(
          assessment,
          ["summary_zh", "assessment_zh"],
          zhText(
            action,
            ["summary_zh"],
            actionAvailable ? "已形成动作表现参考判断。" : "当前视频证据不足，暂时无法评价这个动作。",
          ),
        ),
      ),
    );
    if (detectedSegments !== null) {
      article.append(createText("span", "count-pill", `${detectedSegments} 个动作片段`));
    }
    appendEvaluationDetail(
      article,
      "判断依据：",
      zhText(assessment, ["evidence_zh", "evidence_summary_zh", "meaning_zh"]),
    );
    appendEvaluationDetail(
      article,
      "训练方向：",
      zhText(assessment, ["training_advice_zh", "advice_zh", "priority_zh"]),
    );

    const actionEvaluations = allowedIndicatorEvaluations([
      ...(Array.isArray(action.indicator_evaluations) ? action.indicator_evaluations : []),
      ...(Array.isArray(globalEvaluations) ? globalEvaluations : []),
    ], action.event_code);
    const indicatorSection = document.createElement("section");
    indicatorSection.className = "indicator-section";
    indicatorSection.append(createText("h5", "", "单项表现、证据与建议"));
    if (actionEvaluations.length) {
      const grid = document.createElement("div");
      grid.className = "indicator-grid";
      grid.append(...actionEvaluations.map(renderIndicatorEvaluation));
      indicatorSection.append(grid);
    } else {
      indicatorSection.append(createText("p", "no-indicator-evaluation", "本动作暂无可展示的单项评价。"));
    }
    article.append(indicatorSection);
    return article;
  };

  const setRuntimeBadge = (result, job) => {
    const runtime = asObject(result.runtime);
    const legacyRuntime = asObject(asObject(job.summary).runtime);
    const hasExplicitAccelerator = typeof runtime.accelerator === "string" && runtime.accelerator.trim();
    const accelerator = String(
      runtime.accelerator
      ?? runtime.device
      ?? runtime.device_type
      ?? legacyRuntime.device_used
      ?? "",
    ).toLowerCase();
    const legacyCuda = legacyRuntime.cuda_available;
    const isCpu = accelerator.includes("cpu")
      || (!hasExplicitAccelerator && legacyCuda === false)
      || (!hasExplicitAccelerator && Number.isInteger(runtime.cpu_thread_limit));
    const isGpu = !isCpu && (
      accelerator.includes("gpu")
      || accelerator.includes("cuda")
      || accelerator.includes("tensorrt")
      || (/^\d+$/.test(accelerator) && legacyCuda === true)
      || runtime.accelerator === "gpu"
    );
    runtimeBadge.className = "runtime-badge";
    if (isGpu) {
      runtimeBadge.classList.add("runtime-gpu");
      runtimeBadge.textContent = "GPU 加速";
    } else if (isCpu) {
      runtimeBadge.classList.add("runtime-cpu");
      runtimeBadge.textContent = "CPU 分析";
    } else {
      runtimeBadge.classList.add("runtime-pending");
      runtimeBadge.textContent = "设备待确认";
    }
    const deviceName = typeof runtime.device_name === "string" ? runtime.device_name.trim() : "";
    runtimeBadge.title = deviceName ? `本次使用：${deviceName}` : "本次分析使用的运行设备";
  };

  const renderInsightList = (panel, list, items) => {
    list.replaceChildren();
    items.forEach((item) => list.append(createText("li", "", item)));
    panel.hidden = items.length === 0;
  };

  const publicError = (httpStatus, code) => {
    const message = publicErrorMessagesByCode[code]
      || publicErrorMessagesByStatus[httpStatus]
      || "这次操作没有完成，请稍后重试。";
    const error = new Error("public_request_failed");
    error.httpStatus = httpStatus;
    error.code = code;
    error.publicMessage = message;
    return error;
  };

  const friendlyErrorMessage = (error) => {
    if (error && typeof error.publicMessage === "string") return error.publicMessage;
    if (error && Number.isInteger(error.httpStatus)) {
      return publicErrorMessagesByStatus[error.httpStatus]
        || "这次操作没有完成，请稍后重试。";
    }
    if (error instanceof TypeError) {
      return "无法连接本地分析服务，请确认服务正在运行后重试。";
    }
    return "这次操作没有完成，请稍后重试。";
  };

  const addResultLink = (label, href, primary = false) => {
    if (!href) return;
    const link = document.createElement("a");
    link.href = href;
    link.target = "_blank";
    link.rel = "noreferrer";
    link.textContent = label;
    if (primary) link.className = "primary";
    resultLinks.append(link);
  };

  const addBatchResult = (result, job, context) => {
    const safeId = String(job.id || "result").replace(/[^A-Za-z0-9_-]/g, "");
    const cardId = `batch-result-${safeId}`;
    const article = document.createElement("article");
    article.id = cardId;
    article.className = "batch-result-card";
    const evaluation = asObject(result.training_evaluation);
    const score = scoreOrNull(evaluation.score_0_to_100);
    const evaluationAvailable = evaluation.available !== false && score !== null;
    const title = job.original_filename || `第 ${context?.index || batchResults.children.length + 1} 个视频`;
    const top = document.createElement("div");
    top.className = "batch-result-top";
    top.append(
      createText("strong", "batch-result-name", title),
      createText("span", "batch-result-score", evaluationAvailable ? `${score}/100` : "暂无法评价"),
    );
    const actionText = (Array.isArray(result.actions) ? result.actions : [])
      .filter((action) => supportedActionCodes.has(action.event_code))
      .map((action) => {
        const assessment = asObject(action.performance_assessment);
        const actionScore = scoreOrNull(assessment.score_0_to_100);
        const available = assessment.available !== false && actionScore !== null;
        return `${action.name_zh || "动作"}：${available ? zhText(assessment, ["level_zh"], `${actionScore}/100`) : "暂无法评价"}`;
      })
      .join(" · ");
    const button = createText("button", "secondary compact", "查看详细结果");
    button.type = "button";
    button.addEventListener("click", () => {
      renderResult(result, job, context, false);
      resultState.scrollIntoView({ behavior: "smooth", block: "start" });
    });
    article.append(top, createText("p", "", actionText || "本次暂无可展示的动作评价。"), button);
    const existing = document.getElementById(cardId);
    if (existing) existing.replaceWith(article);
    else batchResults.append(article);
    batchResultsSection.hidden = false;
    batchResultsCount.textContent = `${batchResults.children.length} 个可查看`;
  };

  const addBatchError = (label, error, context) => {
    const article = document.createElement("article");
    article.className = "batch-result-card batch-result-error";
    article.append(
      createText("strong", "batch-result-name", label || `第 ${context?.index || "?"} 个视频`),
      createText("p", "", friendlyErrorMessage(error)),
    );
    batchResults.append(article);
    batchResultsSection.hidden = false;
    batchResultsCount.textContent = `${batchResults.children.length} 个可查看`;
  };

  const renderResult = (result, job, context, addToBatch = true) => {
    showOnly(resultState);
    const prefix = batchPrefix(context);
    stateText.textContent = `${prefix}分析完成`;
    const evaluation = asObject(result.training_evaluation);
    const score = scoreOrNull(evaluation.score_0_to_100);
    const evaluationAvailable = evaluation.available !== false && score !== null;
    scoreLabel.textContent = zhText(evaluation, ["label_zh"], "动作表现参考分（Beta）");
    scoreValue.textContent = evaluationAvailable ? String(score) : "—";
    scoreUnit.textContent = evaluationAvailable ? "/ 100" : "暂无法评价";
    scoreRing.classList.toggle("is-unavailable", !evaluationAvailable);
    scoreRing.style.setProperty("--score-angle", `${evaluationAvailable ? score * 3.6 : 0}deg`);
    resultHeadline.textContent = evaluationAvailable
      ? zhText(evaluation, ["level_zh"], "已形成动作表现参考")
      : "暂无法评价";
    scoreMeaning.textContent = zhText(
      evaluation,
      ["summary_zh"],
      evaluationAvailable
        ? "已根据当前视频形成动作表现参考。"
        : "当前视频证据不足，建议补拍能看清完整身体和动作过程的视频。",
    );

    const strengths = zhList(evaluation.strengths_zh);
    const priorities = zhList(evaluation.priorities_zh);
    renderInsightList(strengthsPanel, strengthsList, strengths);
    renderInsightList(prioritiesPanel, prioritiesList, priorities);
    trainingInsights.hidden = strengths.length === 0 && priorities.length === 0;

    const analysisQuality = asObject(result.analysis_quality || result.display_score);
    const qualityScore = scoreOrNull(analysisQuality.value_0_to_100);
    analysisQualityValue.textContent = qualityScore === null ? "—" : String(qualityScore);
    analysisQualityMeaning.textContent = zhText(
      analysisQuality,
      ["meaning_zh"],
      "表示本次视频中可用于分析的动作信息完整程度，不代表技术水平。",
    );
    const actions = (Array.isArray(result.actions) ? result.actions : [])
      .filter((action) => supportedActionCodes.has(action.event_code));
    const allowedGlobalEvaluations = allowedIndicatorEvaluations([
      ...(Array.isArray(evaluation.indicator_evaluations) ? evaluation.indicator_evaluations : []),
      ...actions.flatMap((action) => Array.isArray(action.indicator_evaluations)
        ? action.indicator_evaluations
        : []),
    ]);
    const evaluatedCount = allowedGlobalEvaluations.filter((item) => scoreOrNull(item.score_0_to_100) !== null).length;
    const meaning = zhText(
      evaluation,
      ["meaning_zh"],
      "Beta 参考分基于当前视频证据，用于辅助训练复盘，不替代现场教练判断。",
    );
    trainingNote.textContent = `${meaning} 本次展示 ${evaluatedCount}/${supportedIndicatorIds.size} 个可评价单项。`;

    actionCards.replaceChildren(...actions.map((action) => renderAction(action, allowedGlobalEvaluations)));
    if (!actions.length) {
      actionCards.append(createText("p", "no-action-evaluation", "本次暂无可展示的动作评价。"));
    }
    setRuntimeBadge(result, job);
    resultLinks.replaceChildren();
    const runtime = asObject(result.runtime);
    const annotatedVideoUrl = asObject(result.artifact_urls)["annotated.mp4"]
      || asObject(job.artifact_urls)["annotated.mp4"];
    if (runtime.annotated_video_generated !== false && annotatedVideoUrl) {
      addResultLink("观看骨架视频", annotatedVideoUrl, true);
    } else {
      resultLinks.append(createText("span", "result-link-note", "本次未生成骨架视频；保持关闭可以减少 CPU 编码开销。"));
    }
    if (addToBatch) addBatchResult(result, job, context);
  };

  const parseResponse = async (response) => {
    let payload = null;
    try {
      payload = await response.json();
    } catch (_) {
      if (!response.ok) throw publicError(response.status);
      throw publicError(502);
    }
    if (!response.ok) {
      const detail = asObject(asObject(payload).detail);
      const code = typeof detail.code === "string"
        ? detail.code
        : typeof asObject(payload).code === "string" ? payload.code : undefined;
      throw publicError(response.status, code);
    }
    if (!payload || typeof payload !== "object") throw publicError(502);
    return payload;
  };

  const pollJob = async (job, context) => {
    while (!["succeeded", "failed"].includes(job.status)) {
      setProgress(job, context);
      await sleep(1000);
      job = await parseResponse(await fetch(`/v1/jobs/${job.id}`, { headers: headers() }));
    }
    setProgress(job, context);
    if (job.status === "failed") throw publicError(undefined, "analysis_failed");
    return job;
  };

  const finishSavedJob = (jobId, succeeded) => {
    const completed = new Set(savedBatch.completed_job_ids);
    const failed = new Set(savedBatch.failed_job_ids);
    if (succeeded) {
      completed.add(jobId);
      failed.delete(jobId);
    } else {
      failed.add(jobId);
      completed.delete(jobId);
    }
    persistBatchState({
      ...savedBatch,
      completed_job_ids: [...completed],
      failed_job_ids: [...failed],
      active_job_id: savedBatch.active_job_id === jobId ? null : savedBatch.active_job_id,
    });
  };

  const fail = (error) => {
    showOnly(errorState);
    stateText.textContent = "分析未完成";
    errorMessage.textContent = friendlyErrorMessage(error);
    submitButton.disabled = false;
    updateFileLabel();
  };

  const formDataForFile = (file) => {
    const data = new FormData(form);
    data.delete("video");
    data.set("video", file, file.name);
    if (!data.get("max_frames")) data.delete("max_frames");
    data.set("write_annotated_video", data.get("write_annotated_video") ? "true" : "false");
    return data;
  };

  const restoreSavedBatch = async () => {
    if (restoreInProgress || !savedBatch.submitted_job_ids.length) return;
    restoreInProgress = true;
    resumeButton.hidden = true;
    submitButton.disabled = true;
    recoveryNote.hidden = false;
    recoveryNote.textContent = "正在恢复已提交的本地任务…";
    let restoredCount = 0;
    let authorizationRequired = false;
    try {
      for (const [position, jobId] of savedBatch.submitted_job_ids.entries()) {
        const context = {
          index: position + 1,
          total: Math.max(savedBatch.total, savedBatch.submitted_job_ids.length),
        };
        try {
          let job = await parseResponse(await fetch(`/v1/jobs/${jobId}`, { headers: headers() }));
          if (["queued", "running"].includes(job.status)) {
            persistBatchState({ ...savedBatch, active_job_id: jobId, current_index: context.index });
            showOnly(progressState);
            job = await pollJob(job, context);
          }
          if (job.status === "failed") {
            finishSavedJob(jobId, false);
            addBatchError(`任务 ${context.index}`, publicError(undefined, "analysis_failed"), context);
            continue;
          }
          const result = await parseResponse(
            await fetch(`/v1/jobs/${jobId}/demo-result`, { headers: headers() }),
          );
          renderResult(result, job, context, true);
          finishSavedJob(jobId, true);
          restoredCount += 1;
        } catch (error) {
          if (error?.httpStatus === 401) {
            authorizationRequired = true;
            throw error;
          }
          addBatchError(`任务 ${context.index}`, error, context);
        }
      }
      const unsentCount = Math.max(0, savedBatch.total - savedBatch.submitted_job_ids.length);
      recoveryNote.textContent = unsentCount
        ? `已恢复 ${restoredCount} 个已提交任务；刷新前尚未提交的 ${unsentCount} 个视频需要重新选择。`
        : `已恢复 ${restoredCount} 个任务结果。`;
    } catch (error) {
      showOnly(errorState);
      errorMessage.textContent = authorizationRequired
        ? "上次任务编号仍已保留。请输入本地访问口令，再点击“恢复上次任务”。"
        : friendlyErrorMessage(error);
      recoveryNote.textContent = "任务编号仍保存在当前浏览器中；访问口令从未保存。";
      resumeButton.hidden = false;
    } finally {
      restoreInProgress = false;
      submitButton.disabled = false;
      updateFileLabel();
    }
  };

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const files = [...(fileInput.files || [])];
    if (!files.length) return;

    batchResults.replaceChildren();
    batchResultsSection.hidden = true;
    recoveryNote.hidden = true;
    resumeButton.hidden = true;
    persistBatchState(emptyBatchState(files.length));
    submitButton.disabled = true;
    submitButton.textContent = "正在分析…";
    let succeededCount = 0;
    let lastSuccess = null;
    let lastError = null;

    for (const [index, file] of files.entries()) {
      const context = { index: index + 1, total: files.length };
      showOnly(progressState);
      stateText.textContent = `${batchPrefix(context)}正在上传`;
      setProgress({
        status: "queued",
        progress: { phase: "queued", percent: 1, message: "正在上传视频" },
      }, context);
      activeJob = null;
      try {
        activeJob = await parseResponse(await fetch("/v1/jobs", {
          method: "POST",
          headers: headers(),
          body: formDataForFile(file),
        }));
        persistBatchState({
          ...savedBatch,
          submitted_job_ids: [...savedBatch.submitted_job_ids, activeJob.id],
          active_job_id: activeJob.id,
          current_index: context.index,
          total: context.total,
        });
        activeJob = await pollJob(activeJob, context);
        const result = await parseResponse(
          await fetch(`/v1/jobs/${activeJob.id}/demo-result`, { headers: headers() }),
        );
        renderResult(result, activeJob, context, true);
        finishSavedJob(activeJob.id, true);
        succeededCount += 1;
        lastSuccess = { result, job: activeJob, context };
      } catch (error) {
        lastError = error;
        if (activeJob?.id && jobIdPattern.test(activeJob.id)) {
          finishSavedJob(activeJob.id, false);
        }
        addBatchError(file.name, error, context);
      }
    }

    activeJob = null;
    submitButton.disabled = false;
    fileInput.value = "";
    updateFileLabel();
    submitButton.textContent = "分析另一批视频";
    if (lastSuccess) {
      renderResult(lastSuccess.result, lastSuccess.job, lastSuccess.context, false);
      stateText.textContent = `本批完成 ${succeededCount}/${files.length} 个视频`;
    } else {
      fail(lastError || publicError(undefined, "analysis_failed"));
    }
  });

  fileInput.addEventListener("change", updateFileLabel);
  ["dragenter", "dragover"].forEach((eventName) => {
    dropZone.addEventListener(eventName, (event) => {
      event.preventDefault();
      dropZone.classList.add("dragging");
    });
  });
  ["dragleave", "drop"].forEach((eventName) => {
    dropZone.addEventListener(eventName, (event) => {
      event.preventDefault();
      dropZone.classList.remove("dragging");
    });
  });
  dropZone.addEventListener("drop", (event) => {
    const files = [...(event.dataTransfer?.files || [])];
    if (!files.length) return;
    const transfer = new DataTransfer();
    files.forEach((file) => transfer.items.add(file));
    fileInput.files = transfer.files;
    updateFileLabel();
  });
  retryButton.addEventListener("click", () => {
    activeJob = null;
    form.reset();
    clearBatchState();
    batchResults.replaceChildren();
    batchResultsSection.hidden = true;
    recoveryNote.hidden = true;
    resumeButton.hidden = true;
    updateFileLabel();
    showOnly(emptyState);
    stateText.textContent = "选择视频后开始";
    runtimeBadge.className = "runtime-badge runtime-pending";
    runtimeBadge.textContent = "设备待确认";
    runtimeBadge.removeAttribute("title");
    submitButton.disabled = false;
  });
  resumeButton.addEventListener("click", restoreSavedBatch);

  const requestedJobId = new URLSearchParams(window.location.search).get("job_id");
  if (requestedJobId && jobIdPattern.test(requestedJobId)) {
    persistBatchState({
      ...emptyBatchState(1),
      submitted_job_ids: [requestedJobId],
      active_job_id: requestedJobId,
    });
  }

  if (savedBatch.submitted_job_ids.length) {
    recoveryNote.hidden = false;
    recoveryNote.textContent = "发现上次已提交的任务，正在尝试恢复；访问口令不会从浏览器存储中读取。";
    resumeButton.hidden = false;
    void restoreSavedBatch();
  }
})();
