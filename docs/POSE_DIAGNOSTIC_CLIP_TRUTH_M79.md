# M79 跳点/左右交换动态视频双盲真值

> 本包已由 M80 v1.2.2 取代。M80 保留第一阶段盲化，并在独立第三方裁决页增加逐帧模型骨架和目标关节轨迹。当前入口见 `docs/POSE_DIAGNOSTIC_CLIP_TRUTH_M80.md`。

## 目标

M79 只处理当前评分中最高频的两类 Pose 诊断阻断：`keypoint_jump_candidates_present` 和 `left_right_swap_candidates_present`。它不修改 Pose、事件、特征或评分质量门禁，而是为后续独立人工评测提供不泄漏候选的动态视频真值入口。

当前真实包位于 `data/annotations/pose-diagnostic-clip-truth-m79-v1.1/`：

- 三段视频；
- 103 个独立 H.264 候选窗口；
- 1,371 个密封候选，其中 jump 1,017、swap 354；
- 1,336 个去重受影响指标实例；
- 9,636 帧动态观察。

103 个窗口各自是独立 MP4，不依赖在 8 分钟原视频中随机 seek。此历史包不再提供可执行的 HTTP 启动命令：旧说明复用了 `serve_pose_scoring_ab.ps1` 并把盲标页、裁决页和密封候选放在同一静态根，不能形成技术上的角色隔离。该脚本现在只服务 `reports/pose-scoring-ab/`，不会服务 M79/M80 或 M77 数据包。不要用 `python -m http.server` 或仓库根静态服务恢复旧入口；需要继续这条人工流程时，应从 M80 建立两个互不包含对方私有文件的独立 allowlist handoff。

## 两阶段独立性

第一阶段由两名不同标注者分别完成。`review.html` 只加载无骨架、无候选帧、无候选关节、无模型坐标的动态片段。每人使用独立 annotator ID 锁定自己的浏览器草稿，逐片段、逐诊断类型声明完整覆盖或不可观测，并记录自己实际观察到的 positive。两人分别导出自己的 coverage 和 positives CSV；不得互相合并或覆盖原始记录。

第二阶段由第三名 reviewer 完成。只有导入两名标注者各自的 coverage/positives 后，`adjudicate.html` 才作为正式裁决入口；此页会显示密封的候选帧与关节，因此不得交给第一阶段标注者提前查看。reviewer ID 必须不同于两名 annotator。`confirmed_true` 至少需要一条可精确匹配候选帧和关节的原始人工 positive；裁决必须精确引用两条 coverage 及全部匹配的原始 positive ID。

浏览器 localStorage 只是草稿，不是真值仓库。必须下载并保留两名标注者各自的 CSV，以及第三人的 `candidate-adjudications.csv`，再合并到包中同名文件。不能把两个 annotator ID 写在同一行来冒充两份独立原始标注。

## 编译与评测

合并三类 CSV 后运行：

```powershell
$env:PYTHONPATH="$PWD\src"
.\.venv\Scripts\python.exe scripts/compile_m79_pose_diagnostic_clip_truth.py `
  --pack data/annotations/pose-diagnostic-clip-truth-m79-v1.1
```

编译器会重新校验：

- M78 审计与 taxonomy SHA；
- 三份 M65 原始诊断队列及其视频、主球员时间线；
- 从原始队列重建的 1,371 个密封候选是否逐项一致；
- 103 个 MP4 的 SHA、codec、帧数和首/中/末帧解码；
- 两名 annotator 是否独立；
- reviewer 是否与两人不同；
- coverage 是否完整覆盖整个窗口；
- positive 与候选的帧、类型、joint/pair 是否精确匹配；
- 所有裁决引用的原始 annotation ID 是否完整、无猜测。

只有 1,371 个候选全部裁决后，报告才输出 jump/swap 的候选 precision。窗口是由模型候选触发的，所以完整时间线 recall 和 F1 永远保持 `null`；需要 recall/F1 时必须另做完整时间线盲标，不能从候选窗口倒推。

## 当前安全状态

当前三个 CSV 只有表头：coverage=0、positive=0、adjudication=0，评测状态为 `annotation_required`，precision/recall/F1 均为 `null`。M79 不自动解除 quality/scoring gate，不改运行候选，不生成 A～E、threshold 或标定资产，也不推进 F2→F3/F4。

浏览器实测使用 Range 服务完成：第一片段 `readyState=4`、错误为空、逐帧从源帧 3 前进到 4；最长视频可直接切到第 77/77 个片段并加载源帧 11,450；裁决页首候选直接定位到片段内 0.766666 秒。两个页面控制台均无本地应用错误。
