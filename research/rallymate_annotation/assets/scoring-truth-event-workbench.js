"use strict";

(() => {
  const STATUS = "technical_handoff_verified_external_protocol_required";
  const bootstrapNode = document.getElementById("scoring-truth-event-bootstrap");
  const statusNode = document.getElementById("technical-status");
  const video = document.getElementById("workbench-video");
  const videoSelect = document.getElementById("video-select");
  const timeReadout = document.getElementById("time-readout");
  const seekSlider = document.getElementById("seek-slider");
  const playPause = document.getElementById("play-pause");

  const failClosed = (message) => {
    document.querySelectorAll("button, input, select, textarea").forEach((node) => {
      node.disabled = true;
    });
    statusNode.textContent = `技术校验失败：${message}`;
    statusNode.dataset.state = "invalid";
  };

  let contract;
  try {
    contract = JSON.parse(bootstrapNode.textContent);
  } catch (_error) {
    failClosed("启动契约不是有效 JSON");
    return;
  }

  if (
    contract.status !== STATUS ||
    contract.annotation_execution_authorized !== false ||
    contract.external_protocol_receipt_verified !== false ||
    contract.mutation_enabled !== false ||
    contract.import_enabled !== false ||
    contract.export_enabled !== false ||
    !Array.isArray(contract.tasks) ||
    contract.tasks.length !== 3
  ) {
    failClosed("外部协议门禁或三视频任务契约不一致");
    return;
  }

  document.querySelectorAll("[data-mutation]").forEach((node) => {
    node.disabled = true;
    node.setAttribute("aria-disabled", "true");
  });

  const taskById = new Map();
  contract.tasks.forEach((task, index) => {
    taskById.set(task.task_id, task);
    const option = document.createElement("option");
    option.value = task.task_id;
    option.textContent = `视频 ${index + 1} · ${task.video_id}`;
    videoSelect.appendChild(option);
  });

  const activeTask = () => taskById.get(videoSelect.value);
  const duration = () => (Number.isFinite(video.duration) ? video.duration : 0);
  const clampTime = (seconds) => Math.max(0, Math.min(duration(), seconds));
  const frameSeconds = () => {
    const task = activeTask();
    return task.frame_rate.denominator / task.frame_rate.numerator;
  };
  const renderTime = () => {
    const current = Number.isFinite(video.currentTime) ? video.currentTime : 0;
    const total = duration();
    timeReadout.textContent = `${current.toFixed(3)} s / ${total.toFixed(3)} s`;
    seekSlider.value = String(current);
    seekSlider.max = String(total);
  };

  const loadTask = () => {
    const task = activeTask();
    if (!task) {
      failClosed("任务不存在");
      return;
    }
    video.pause();
    video.src = task.media_path;
    video.load();
    document.getElementById("active-task-id").textContent = task.task_id;
    document.getElementById("active-video-id").textContent = task.video_id;
    document.getElementById("frame-rate").textContent =
      `${task.frame_rate.numerator}/${task.frame_rate.denominator} fps`;
    renderTime();
  };

  videoSelect.addEventListener("change", loadTask);
  video.addEventListener("loadedmetadata", renderTime);
  video.addEventListener("durationchange", renderTime);
  video.addEventListener("timeupdate", renderTime);
  video.addEventListener("seeked", renderTime);

  playPause.addEventListener("click", async () => {
    if (video.paused) {
      try {
        await video.play();
      } catch (_error) {
        statusNode.textContent = "浏览器阻止自动播放；可使用视频原生控件继续技术检查。";
      }
    } else {
      video.pause();
    }
  });

  document.querySelectorAll("[data-step-frames]").forEach((button) => {
    button.addEventListener("click", () => {
      video.pause();
      const delta = Number(button.dataset.stepFrames) * frameSeconds();
      video.currentTime = clampTime(video.currentTime + delta);
      renderTime();
    });
  });

  seekSlider.addEventListener("input", () => {
    video.pause();
    video.currentTime = clampTime(Number(seekSlider.value));
    renderTime();
  });

  statusNode.textContent = "技术检查模式：播放、拖动和逐帧可用；所有写入、导入和导出均已锁定。";
  statusNode.dataset.state = "valid";
  loadTask();

  window.RallyMateScoringTruthEventHandoff = Object.freeze({
    status: STATUS,
    taskCount: contract.tasks.length,
    annotationExecutionAuthorized: false,
    externalProtocolReceiptVerified: false,
  });
})();
