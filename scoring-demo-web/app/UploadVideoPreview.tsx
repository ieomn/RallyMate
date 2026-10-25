"use client";

import { useId, useState } from "react";

function durationLabel(seconds: number) {
  const whole = Math.round(seconds);
  return whole < 60 ? `${whole} 秒` : `${Math.floor(whole / 60)} 分 ${whole % 60} 秒`;
}

/** Uses the existing selected-file object URL; never uploads or persists media. */
export default function UploadVideoPreview({ file, src }: { file: File; src: string | null }) {
  return <Preview key={`${file.name}:${file.size}:${file.lastModified}`} file={file} src={src} />;
}

function Preview({ file, src }: { file: File; src: string | null }) {
  const [metadata, setMetadata] = useState<{ duration: number; width: number; height: number } | null>(null);
  const [failed, setFailed] = useState(false);
  const [checks, setChecks] = useState<Set<number>>(new Set());
  const id = useId();
  const items = ["全身、双脚和持拍手持续在画面内", "包含动作前的准备和动作后的恢复", "机位稳定，使用正常速度的原片"];
  return <section className="upload-video-preview" aria-labelledby={`${id}-title`}>
    <div className="uvp-media">{src && !failed ? <video controls playsInline preload="metadata" src={src} aria-label="已选择视频的本地预览" onLoadedMetadata={event => {
      const video = event.currentTarget;
      if (Number.isFinite(video.duration) && video.duration > 0) setMetadata({ duration: video.duration, width: video.videoWidth, height: video.videoHeight });
    }} onError={() => setFailed(true)}><track kind="captions" /></video> : <p>{failed ? "此浏览器暂不能预览这种视频编码。你仍可提交，由分析服务检查文件。" : "正在准备本地预览…"}</p>}</div>
    <div className="uvp-copy"><h3 id={`${id}-title`}>上传前，看一眼画面</h3><p className="uvp-file-name">{file.name}</p><p className="uvp-meta" aria-live="polite">{metadata ? `${durationLabel(metadata.duration)} · ${metadata.width} × ${metadata.height}` : failed ? "时长暂时无法读取" : "正在读取时长…"} · {(file.size / (1024 * 1024)).toFixed(1)} MB</p>
      <p className="uvp-scope">现在只在本机预览；点击“开始分析”后才会上传。</p>
      <div className="uvp-checks">{items.map((item, index) => <label key={item}><input type="checkbox" checked={checks.has(index)} onChange={() => setChecks(previous => { const next = new Set(previous); if (next.has(index)) next.delete(index); else next.add(index); return next; })} /><span>{item}</span></label>)}</div>
      <p className="uvp-help">这些是你对画面的自检，不是自动检测结果，也不影响上传。</p>
      <p className="uvp-tip">{metadata && metadata.duration > 30 ? "这段视频较长。首次建议先选 10–30 秒、围绕一个主题且保留完整动作的片段，以减少等待。" : "首次建议选 10–30 秒、围绕一个主题且保留完整动作的片段。"}这是使用建议，不是时长限制。分析可能明显长于视频本身，等待时间取决于片长、画面和服务负载；页面会显示当前阶段。</p>
    </div>
  </section>;
}
