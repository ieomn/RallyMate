# M80 跳点/左右交换动态视频双盲 + 骨架裁决

## 目标

M80 修复 M79 第三方裁决页的关键可用性缺口：原页面虽然解封候选帧和关节名称，但没有显示模型预测坐标，reviewer 无法直观看出关键点跳到了哪里、左右点是否真的互换。M80 保留第一阶段完全盲化，只在独立第三方页面加载与视频逐帧同步的模型骨架、目标关节和前后轨迹。

当前不可变包：`data/annotations/pose-diagnostic-clip-truth-m80-v1.2.2/`。

- 3 段视频；
- 103 个独立 H.264 动态片段；
- 1,371 个密封候选：jump 1,017、swap 354；
- 1,336 个去重受影响指标实例；
- 9,636 帧观察；
- 103 份 adjudicator-only Pose evidence，覆盖相同 9,636 帧，拓扑为 Halpe26。

## 两阶段隔离

第一阶段入口：

`http://127.0.0.1:8765/data/annotations/pose-diagnostic-clip-truth-m80-v1.2.2/review.html`

两名不同 annotator 分别观看无骨架、无候选帧、无候选关节、无模型坐标的片段，各自导出 coverage 与 positives CSV。第一阶段 bootstrap 不包含 pose evidence URL、候选 ID 或候选位置。

第二阶段入口：

`http://127.0.0.1:8765/data/annotations/pose-diagnostic-clip-truth-m80-v1.2.2/adjudicate.html`

第三名 reviewer 导入两名 annotator 的四份原始 CSV 后，才可逐候选查看：

- 完整 Halpe26 骨架；
- 当前候选目标 joint 或左右 joint pair；
- 目标点前后各 4 帧轨迹；
- 每点置信度、源帧、Track 和 Pose 状态；
- 可调显示置信度、完整骨架和目标轨迹开关。

每份 pose evidence 都绑定 `frames.jsonl` 与 `primary-player.jsonl` SHA。页面加载时再次校验 sidecar 文件 SHA-256；Python validator 还会从源 frames 和主球员 timeline 逐帧重建并进行 exact replay。即使同时修改 sidecar 和自声明 hash，也不能通过。

模型骨架只是待评测输出，不是真值。它只在第三方裁决阶段用于解释候选，不会提供给两名盲标 annotator。

## 编译

合并两名 annotator 和第三方 reviewer 的 CSV 后运行：

```powershell
$env:PYTHONPATH="$PWD\src"
python scripts/compile_m80_pose_diagnostic_clip_truth.py `
  --pack data/annotations/pose-diagnostic-clip-truth-m80-v1.2.2
```

只有全部 1,371 个候选完成独立裁决时，才能计算 candidate precision。候选窗口没有完整时间线负例覆盖，因此 recall 和 F1 永远保持 null；需要它们时必须另做全时间线盲标。

## 当前状态

当前 coverage、positive、adjudication 均为 0，状态为 `annotation_required`，所有诊断指标均为 null。M80 不修改运行时 quality/scoring gate，不生成 A～E、threshold 或标定资产，也不推进 F2→F3/F4。

浏览器已实测：两个页面视频均 `readyState=4` 且无错误；裁决页 sidecar SHA 校验通过，canvas 为 320×568，骨架与候选源帧同步；第一阶段无候选或 Pose evidence 泄漏。

最终验证：定向测试 7/7；全仓 `scripts/run_tests.ps1` 共 670 项通过、16 项因可选 `jsonschema` 依赖未安装而跳过；Python compileall、两份浏览器 JS 语法检查、103/103 sidecar Schema、主报告 225 个本地引用和 103/103 媒体 SHA 对照均通过。
