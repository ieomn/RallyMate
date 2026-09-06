(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const ui = {
    selector: $("video-selector"), summary: $("summary-strip"), video: $("source-video"),
    canvas: $("pose-canvas"), stage: $("video-stage"), timeline: $("timeline"),
    hudTime: $("hud-time"), hudFrame: $("hud-frame"), hudEvent: $("hud-event"), hudTrack: $("hud-track"),
    model: $("model-label"), previous: $("previous-event"), next: $("next-event"),
    eventCode: $("event-code"), eventTitle: $("event-title"), eventConfidence: $("event-confidence"),
    phases: $("phase-strip"), diagnostics: $("event-diagnostics"), indicators: $("indicator-list"),
    selectedId: $("selected-indicator-id"), selectedTitle: $("selected-indicator-title"),
    selectedStatus: $("selected-status"), features: $("feature-list"), reasons: $("reason-list"),
    skeleton: $("toggle-skeleton"), trail: $("toggle-trail"), confidence: $("toggle-confidence"),
    threshold: $("confidence-range"), thresholdOutput: $("confidence-output"), fatal: $("fatal-error")
  };
  const state = { manifest: null, entry: null, data: null, eventIndex: 0, indicatorId: null, raf: null };
  const EVENT_TITLES = { FS01: "分腿垫步候选", FS02: "方向启动候选", FS09: "制动与重新稳定候选" };
  const PHASE_LABELS = {
    preload_ms: "预加载", takeoff_proxy_ms: "双脚上抬代理", landing_proxy_ms: "双脚减速代理",
    redistribution_ms: "重心重新分配", initiation_ms: "启动", direction_conversion_ms: "方向转换",
    support_extension_proxy_ms: "支撑侧伸展", lead_foot_motion_onset_proxy_ms: "启动脚运动",
    first_step_slowdown_proxy_ms: "第一步减速", peak_speed_ms: "速度峰值",
    deceleration_peak_ms: "减速峰", restabilization_ms: "重新稳定", stable_control_ms: "稳定控制"
  };
  const REASON_LABELS = {
    coach_calibration_missing: "缺少多教练 A～E / 排序标定",
    independent_test_missing: "尚未通过封存独立测试",
    event_ground_truth_missing: "候选事件尚无人工边界真值",
    keypoint_jump_diagnostic_unverified: "相关关节存在跳点候选，尚未人工裁决",
    left_right_assignment_unverified: "相关左右关节可能交换，尚未人工裁决",
    tactical_target_direction_required: "缺少人工目标方向语义",
    event_identity_continuity_unverified: "事件内身份连续性尚未验证",
    required_pose_phase_proxy_not_observed: "指标需要的 Pose 阶段代理未观察到",
    primary_pose_coverage_low: "事件内主球员 Pose 覆盖不足",
    event_boundary_censored: "事件边界被视频起止位置截断"
  };
  const EDGES = [
    ["nose","left_eye"],["nose","right_eye"],["left_eye","left_ear"],["right_eye","right_ear"],
    ["neck","left_shoulder"],["neck","right_shoulder"],["left_shoulder","left_elbow"],["left_elbow","left_wrist"],
    ["right_shoulder","right_elbow"],["right_elbow","right_wrist"],["neck","hip"],["hip","left_hip"],["hip","right_hip"],
    ["left_hip","left_knee"],["left_knee","left_ankle"],["right_hip","right_knee"],["right_knee","right_ankle"],
    ["left_ankle","left_heel"],["left_ankle","left_big_toe"],["left_big_toe","left_small_toe"],
    ["right_ankle","right_heel"],["right_ankle","right_big_toe"],["right_big_toe","right_small_toe"]
  ];

  function stable(value) {
    if (Array.isArray(value)) return `[${value.map(stable).join(",")}]`;
    if (value && typeof value === "object") return `{${Object.keys(value).sort().map(k => `${JSON.stringify(k)}:${stable(value[k])}`).join(",")}}`;
    return JSON.stringify(value);
  }
  async function sha256(text) {
    const bytes = new TextEncoder().encode(text);
    const hash = await crypto.subtle.digest("SHA-256", bytes);
    return Array.from(new Uint8Array(hash), byte => byte.toString(16).padStart(2, "0")).join("").toUpperCase();
  }
  async function verifyContent(payload, expected) {
    const copy = structuredClone(payload);
    const declared = copy.content_sha256;
    delete copy.content_sha256;
    const actual = await sha256(stable(copy));
    if (actual !== declared || (expected && actual !== expected)) throw new Error("可视化数据内容哈希校验失败");
  }
  function formatTime(ms) {
    const total = Math.max(0, Number(ms) || 0);
    const minutes = Math.floor(total / 60000);
    const seconds = Math.floor((total % 60000) / 1000);
    const millis = Math.floor(total % 1000);
    return `${String(minutes).padStart(2,"0")}:${String(seconds).padStart(2,"0")}.${String(millis).padStart(3,"0")}`;
  }
  function statusText(value) {
    return ({ measured: "已测量", unavailable: "不可用", calibration_required: "待标定", pass: "通过", advisory: "需复核", hard_fail: "硬阻断" })[value] || value || "—";
  }
  function formatValue(feature) {
    if (!feature.valid || feature.value === null || feature.value === undefined) return "unavailable";
    if (typeof feature.value === "number") return `${Number(feature.value.toFixed(4))} ${feature.unit || ""}`.trim();
    return `${feature.value} ${feature.unit || ""}`.trim();
  }
  function nearestFrameIndex(ms) {
    const frames = state.data.frames;
    let lo = 0, hi = frames.length - 1;
    while (lo < hi) {
      const mid = Math.floor((lo + hi) / 2);
      if (frames[mid].t < ms) lo = mid + 1; else hi = mid;
    }
    if (lo > 0 && Math.abs(frames[lo - 1].t - ms) <= Math.abs(frames[lo].t - ms)) return lo - 1;
    return lo;
  }
  function activeEventIndex(ms) {
    const current = state.data.events[state.eventIndex];
    if (current && ms >= current.start_ms && ms <= current.end_ms) return state.eventIndex;
    const exact = state.data.events.findIndex(event => ms >= event.start_ms && ms <= event.end_ms);
    if (exact >= 0) return exact;
    let best = 0, distance = Infinity;
    state.data.events.forEach((event, index) => {
      const d = Math.min(Math.abs(ms - event.start_ms), Math.abs(ms - event.end_ms));
      if (d < distance) { best = index; distance = d; }
    });
    return best;
  }
  function selectedIndicator() {
    const event = state.data.events[state.eventIndex];
    return event?.indicators.find(item => item.indicator_id === state.indicatorId) || event?.indicators[0] || null;
  }
  function pointColor(name, selected, diagnostic) {
    if (diagnostic) return diagnostic === "jump" ? "#ff5164" : "#d18cff";
    if (selected) return "#ffd166";
    if (name.startsWith("left_")) return "#52d89c";
    if (name.startsWith("right_")) return "#ff9e64";
    return "#55b7ff";
  }
  function drawFrame(index) {
    const data = state.data;
    if (!data) return;
    const frame = data.frames[index];
    const canvas = ui.canvas;
    const rect = ui.video.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    const width = Math.max(1, Math.round(rect.width * dpr));
    const height = Math.max(1, Math.round(rect.height * dpr));
    if (canvas.width !== width || canvas.height !== height) { canvas.width = width; canvas.height = height; }
    const ctx = canvas.getContext("2d");
    ctx.clearRect(0, 0, width, height);
    if (!frame.points) return;
    const threshold = Number(ui.threshold.value);
    const nameIndex = new Map(data.joint_names.map((name, i) => [name, i]));
    const indicator = selectedIndicator();
    const selected = new Set(indicator?.required_joints || []);
    const jump = new Set(frame.jump || []), swap = new Set(frame.swap || []);
    const point = (name, f = frame) => {
      const i = nameIndex.get(name), p = i === undefined ? null : f.points?.[i];
      if (!p || p[0] === null || p[1] === null || (p[2] ?? 0) < threshold) return null;
      return [p[0] * width, p[1] * height, p[2]];
    };
    ctx.lineCap = "round"; ctx.lineJoin = "round";
    if (ui.skeleton.checked) {
      EDGES.forEach(([a, b]) => {
        const pa = point(a), pb = point(b); if (!pa || !pb) return;
        const important = selected.has(a) || selected.has(b);
        ctx.strokeStyle = important ? "rgba(255,209,102,.95)" : "rgba(85,183,255,.78)";
        ctx.lineWidth = (important ? 4 : 2.2) * dpr;
        ctx.beginPath(); ctx.moveTo(pa[0], pa[1]); ctx.lineTo(pb[0], pb[1]); ctx.stroke();
      });
    }
    if (ui.trail.checked && selected.size) {
      selected.forEach(name => {
        const points = [];
        for (let offset = 6; offset >= 0; offset--) {
          const trailFrame = data.frames[Math.max(0, index - offset)];
          const p = point(name, trailFrame); if (p) points.push(p);
        }
        if (points.length > 1) {
          ctx.strokeStyle = "rgba(255,209,102,.62)"; ctx.lineWidth = 3 * dpr;
          ctx.beginPath(); points.forEach((p, i) => i ? ctx.lineTo(p[0], p[1]) : ctx.moveTo(p[0], p[1])); ctx.stroke();
        }
      });
    }
    data.joint_names.forEach(name => {
      const p = point(name); if (!p) return;
      const diagnostic = jump.has(name) ? "jump" : (swap.has(name) ? "swap" : null);
      const important = selected.has(name) || diagnostic;
      ctx.fillStyle = pointColor(name, selected.has(name), diagnostic);
      ctx.beginPath(); ctx.arc(p[0], p[1], (important ? 5 : 3.2) * dpr, 0, Math.PI * 2); ctx.fill();
      if (diagnostic) { ctx.strokeStyle = "#fff"; ctx.lineWidth = 1.5 * dpr; ctx.stroke(); }
      if (ui.confidence.checked && (important || p[2] < .45)) {
        ctx.font = `${11 * dpr}px system-ui`; ctx.fillStyle = "#fff";
        ctx.fillText(`${name} ${p[2].toFixed(2)}`, p[0] + 7 * dpr, p[1] - 7 * dpr);
      }
    });
  }
  function renderSummary() {
    const d = state.data, measured = d.summary.feature_status_counts.measured || 0;
    const unavailable = d.summary.feature_status_counts.unavailable || 0;
    const calibration = d.summary.scoring_status_counts.calibration_required || 0;
    const scoreUnavailable = d.summary.scoring_status_counts.unavailable || 0;
    const items = [
      ["视频帧", d.video.frame_count.toLocaleString()], ["候选事件", d.summary.event_count.toLocaleString()],
      ["特征向量", `${measured} 已测 / ${unavailable} 不可用`], ["评分状态", `${calibration} 待标定 / ${scoreUnavailable} 不可用`],
      ["正式等级", `${d.summary.grade_count} 个 A～E`]
    ];
    ui.summary.innerHTML = items.map(([label, value]) => `<div class="summary-item"><span>${label}</span><strong>${value}</strong></div>`).join("");
  }
  function renderTimeline() {
    ui.timeline.innerHTML = "";
    const duration = Math.max(1, state.data.video.duration_ms);
    ["FS01","FS02","FS09"].forEach(code => {
      const lane = document.createElement("div"); lane.className = "timeline-lane";
      const label = document.createElement("div"); label.className = "lane-label"; label.textContent = code;
      const track = document.createElement("div"); track.className = "lane-track";
      state.data.events.forEach((event, index) => {
        if (event.event_code !== code) return;
        const button = document.createElement("button");
        button.type = "button"; button.className = `event-block ${code.toLowerCase()}`;
        button.style.left = `${event.start_ms / duration * 100}%`;
        button.style.width = `${Math.max(.18, (event.end_ms - event.start_ms) / duration * 100)}%`;
        button.setAttribute("aria-label", `${code} ${formatTime(event.start_ms)} 至 ${formatTime(event.end_ms)}`);
        button.dataset.eventIndex = String(index);
        button.addEventListener("click", () => selectEvent(index, true));
        track.appendChild(button);
      });
      lane.append(label, track); ui.timeline.appendChild(lane);
    });
    const playhead = document.createElement("div"); playhead.className = "timeline-playhead"; playhead.id = "timeline-playhead";
    playhead.style.left = "56px"; ui.timeline.appendChild(playhead);
  }
  function renderEvent() {
    const event = state.data.events[state.eventIndex];
    if (!event) return;
    ui.eventCode.textContent = `${event.event_code} · ${event.event_id}`;
    ui.eventTitle.textContent = EVENT_TITLES[event.event_code] || event.event_code;
    ui.eventConfidence.textContent = `候选置信度 ${event.confidence?.toFixed(2) ?? "—"}`;
    ui.phases.innerHTML = Object.entries(event.key_phases_ms).map(([key, value]) => `<span class="phase-chip">${PHASE_LABELS[key] || key} ${value === null ? "未观察" : formatTime(value)}</span>`).join("");
    const t = event.track_diagnostics;
    ui.diagnostics.textContent = `Track 覆盖 ${Math.round((t.track_coverage_fraction || 0) * 100)}% · Pose 覆盖 ${Math.round((t.pose_coverage_fraction || 0) * 100)}% · 跳点候选帧 ${t.keypoint_jump_candidate_frames.length} · 左右交换候选帧 ${t.left_right_swap_candidate_frames.length} · 最长 Pose 缺失 ${t.longest_pose_missing_ms} ms`;
    ui.indicators.innerHTML = "";
    if (!event.indicators.some(item => item.indicator_id === state.indicatorId)) state.indicatorId = event.indicators[0]?.indicator_id || null;
    event.indicators.forEach(item => {
      const button = document.createElement("button"); button.type = "button";
      button.className = `indicator-row${item.indicator_id === state.indicatorId ? " selected" : ""}`;
      button.innerHTML = `<span class="indicator-title"><strong>${item.label}</strong><span>${item.indicator_id} · ${item.feasibility_level}</span></span><span class="status-pair"><span class="state ${item.feature_status}">特征 ${statusText(item.feature_status)}</span><span class="state ${item.scoring_status}">评分 ${statusText(item.scoring_status)}</span></span>`;
      button.addEventListener("click", () => { state.indicatorId = item.indicator_id; renderEvent(); drawNow(); });
      ui.indicators.appendChild(button);
    });
    renderIndicator();
    document.querySelectorAll(".event-block").forEach(block => block.classList.toggle("selected", Number(block.dataset.eventIndex) === state.eventIndex));
  }
  function renderIndicator() {
    const item = selectedIndicator(); if (!item) return;
    ui.selectedId.textContent = `${item.indicator_id} · ${item.feasibility_level}`;
    ui.selectedTitle.textContent = item.label;
    ui.selectedStatus.innerHTML = `<span class="status-badge ${item.feature_status}">特征 ${statusText(item.feature_status)}</span><span class="status-badge ${item.scoring_status}">评分 ${statusText(item.scoring_status)}</span><span class="status-badge">质量门禁 ${statusText(item.quality_status)}</span>`;
    ui.features.innerHTML = item.features.map(feature => `<div class="feature-row"><span class="feature-name">${feature.name}</span><span class="feature-value${feature.valid ? "" : " invalid"}">${formatValue(feature)}</span><span class="feature-meta">${feature.valid ? `confidence ${feature.confidence ?? "—"} · 证据帧 ${feature.source_frames.join(", ") || "—"}` : feature.reason || "缺少观测"}</span></div>`).join("");
    const reasons = [...item.hard_fail_flags.map(x => [x, true]), ...item.scoring_block_flags.map(x => [x, true]), ...item.reason_codes.map(x => [x, false])];
    ui.reasons.innerHTML = reasons.length ? reasons.map(([reason, block]) => `<div class="reason-item${block ? " block" : ""}">${REASON_LABELS[reason] || reason}</div>`).join("") : `<div class="reason-item">当前事件特征和评分证据门禁已通过；仍需外部标定与独立测试。</div>`;
  }
  function selectEvent(index, seek) {
    state.eventIndex = Math.max(0, Math.min(state.data.events.length - 1, index));
    state.indicatorId = state.data.events[state.eventIndex]?.indicators[0]?.indicator_id || null;
    if (seek) ui.video.currentTime = state.data.events[state.eventIndex].start_ms / 1000;
    renderEvent(); drawNow();
  }
  function updateHud(index) {
    const frame = state.data.frames[index], ms = frame.t;
    ui.hudTime.textContent = formatTime(ms); ui.hudFrame.textContent = `源帧 ${frame.s} / 处理帧 ${frame.i}`;
    ui.hudTrack.textContent = `Track ${frame.track ?? "—"} · Pose ${frame.pose_confidence ?? "—"}`;
    const event = state.data.events[state.eventIndex]; ui.hudEvent.textContent = event ? `${event.event_code} · ${event.event_id}` : "无活动事件";
    const playhead = $("timeline-playhead"); if (playhead) playhead.style.left = `calc(56px + (100% - 56px) * ${Math.min(1, ms / Math.max(1, state.data.video.duration_ms))})`;
  }
  function drawNow() {
    if (!state.data) return;
    const ms = Math.round((ui.video.currentTime || 0) * 1000);
    const nextEvent = activeEventIndex(ms);
    if (nextEvent !== state.eventIndex) { state.eventIndex = nextEvent; state.indicatorId = null; renderEvent(); }
    const index = nearestFrameIndex(ms); updateHud(index); drawFrame(index);
  }
  function fitVideoStage() {
    if (!ui.video.videoWidth || !ui.video.videoHeight) return;
    const aspect = ui.video.videoWidth / ui.video.videoHeight;
    ui.stage.style.aspectRatio = `${ui.video.videoWidth} / ${ui.video.videoHeight}`;
    ui.stage.style.maxWidth = `${Math.max(260, Math.round(window.innerHeight * 0.72 * aspect))}px`;
    drawNow();
  }
  function animationLoop() { drawNow(); if (!ui.video.paused && !ui.video.ended) state.raf = requestAnimationFrame(animationLoop); }
  async function loadVideo(entry) {
    state.entry = entry;
    const response = await fetch(entry.data_file, { cache: "no-store" });
    if (!response.ok) throw new Error(`视频数据加载失败：HTTP ${response.status}`);
    const raw = await response.text();
    if (await sha256(raw) !== entry.data_file_sha256) throw new Error("视频观察数据文件 SHA-256 校验失败");
    const data = JSON.parse(raw);
    if (data.content_sha256 !== entry.content_sha256) throw new Error("视频观察数据内容绑定不一致");
    state.data = data;
    state.eventIndex = 0; state.indicatorId = data.events[0]?.indicators[0]?.indicator_id || null;
    ui.video.pause(); ui.video.src = data.video_href; ui.video.load();
    ui.model.textContent = `${data.model.pose_profile} · ${data.model.native_keypoint_format}/${data.model.native_keypoint_count} 点 · ${data.model.event}`;
    renderSummary(); renderTimeline(); renderEvent(); renderSelector();
  }
  function renderSelector() {
    ui.selector.innerHTML = "";
    state.manifest.videos.forEach(entry => {
      const button = document.createElement("button"); button.type = "button";
      button.setAttribute("aria-pressed", String(entry.video_id === state.entry?.video_id));
      button.innerHTML = `${entry.display_name}<small>${entry.frame_count.toLocaleString()} 帧 · ${entry.event_count} 事件</small>`;
      button.addEventListener("click", () => loadVideo(entry).catch(fatal)); ui.selector.appendChild(button);
    });
  }
  function fatal(error) { console.error(error); ui.fatal.hidden = false; ui.fatal.textContent = `动态评分观察器无法继续：\n${error.stack || error}`; }
  async function boot() {
    const response = await fetch("manifest.json", { cache: "no-store" });
    if (!response.ok) throw new Error(`manifest 加载失败：HTTP ${response.status}`);
    state.manifest = await response.json(); renderSelector();
    await loadVideo(state.manifest.videos[0]);
  }
  ui.video.addEventListener("loadedmetadata", fitVideoStage);
  ui.video.addEventListener("seeked", drawNow); ui.video.addEventListener("timeupdate", drawNow);
  ui.video.addEventListener("play", () => { cancelAnimationFrame(state.raf); animationLoop(); });
  ui.video.addEventListener("pause", () => { cancelAnimationFrame(state.raf); drawNow(); });
  ui.video.addEventListener("error", () => fatal(new Error("视频无法解码或加载，请确认使用 http://127.0.0.1:8765 访问")));
  ui.previous.addEventListener("click", () => selectEvent(state.eventIndex - 1, true));
  ui.next.addEventListener("click", () => selectEvent(state.eventIndex + 1, true));
  [ui.skeleton, ui.trail, ui.confidence].forEach(input => input.addEventListener("change", drawNow));
  ui.threshold.addEventListener("input", () => { ui.thresholdOutput.value = Number(ui.threshold.value).toFixed(2); drawNow(); });
  window.addEventListener("resize", fitVideoStage); boot().catch(fatal);
})();
