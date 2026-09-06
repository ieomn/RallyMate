# M81 三视频动态评分观察器

入口：`http://127.0.0.1:8765/reports/scoring-visual-observer-m81/index.html`

M81 把当前三段真实视频的 RTMPose-M/Halpe26 输出、主球员 Track、FS01/FS02/FS09 候选事件、13 项 F2 指标、版本化特征和质量门禁放在同一播放器中。它解决的是“报告能列出事实，但很难把数值与画面中的具体动作对上”的观察问题。

当前 `dynamic-scoring-observer-v1.1.0` 由
`reports/scoring-candidate-multivideo-m78/runs` 显式生成。生成器不再设置历史
run root 默认值，调用者必须明确选择来源，避免在报告外观和计数未变化时静默继续读取旧 bundle。

## 可以直接观察什么

- 播放或拖动原视频时，26 点全身骨架逐帧同步；左侧、右侧和当前指标依赖关节使用不同颜色。
- 选择某项指标后，页面只强调其 required feature 实际依赖的关节，并显示最近 6 帧轨迹。
- FS01、FS02、FS09 使用三条时间轴；点击任一候选色块可直接跳到事件开始位置。
- 当前事件展示关键阶段、候选置信度、Track/Pose 覆盖、跳点、左右交换和最长缺失。
- 每条指标同时显示 `feature_status` 与 `scoring_status`，避免把“特征已测量”误认为“已经能够给 A～E”。
- 每项 required feature 显示 value、unit、confidence、valid/reason 和 source_frames；阻断原因使用可读中文解释。

## 当前真实范围

- 3 段完整视频；
- 15,868 帧 Halpe26；
- 546 个规则候选事件；
- 2,366 条指标×事件实例；
- 2,291 条 feature measured、75 条 feature unavailable；
- 834 条 scoring calibration_required、1,532 条 scoring unavailable；
- 0 个 A～E、0 个 threshold、没有跨事件或跨指标总分。

上面只是当前三段输入上的计算与门禁覆盖，不是事件准确率、关键点准确率或动作质量结论。
M78 来源本身仍是 F2 候选事件与测量产物；M81 不提供人工真值、F3 标定、独立测试或正式发布授权。

## 数据与防篡改

生成命令：

```powershell
$env:PYTHONPATH="$PWD\src"
python scripts/build_scoring_visual_observer_m81.py `
  --runs-root "$PWD\reports\scoring-candidate-multivideo-m78\runs" `
  --replace
```

每个视频数据文件绑定原视频、`frames.jsonl`、`primary-player.jsonl`、`events.jsonl`、`indicator-features.jsonl`、`scores.jsonl` 和 scoring summary 的 SHA-256。顶层 manifest 还逐视频保存 `source_summary_sha256`，Python validator 必须确认它与对应 payload 的 `source_sha256.summary` 完全相同。当前三份 M78 summary 文件哈希为：

- `3ae77ee3271d67de171585a5c39ddd69`：`5204067FD36DECACD25B48BAF0477ED7FF26FB4AD321DDF1BBF70B67662D5F73`；
- `850cb0006b406c7176eeda8d711cd065`：`278F599C22939CF333C2BAD2380D87E4D6C03B0910FD7BEFDA1FD30185DBA14B`；
- `8d7754d0de6d315674013d5b69a0b6ba`：`C438DABEF089FCD09D20B9AECB02FA4DB2CC261181F1497DB618C7C1EFD1B71C`。

浏览器先校验实际下载数据文件 SHA-256；Python validator 还会校验 canonical content hash、13 项集合、连续帧、事件指标数量、feature order 以及 grade/threshold 为零。生成时读取当前 registry 并校验指标集合与 required feature 顺序，但 M81 不把普通报告目录提升为受控 registry 或发布机构。

模型骨架是待评测输出，事件区间是规则候选，二者都不是人工真值。观察器不改变任何 event、feature、quality/scoring gate、成熟度或标定资产。

这些哈希只在当前工作区内建立可重放的来源链并阻止无意的旧来源漂移。M81、M78 和它们所在的 `reports` 目录都不是操作员控制的可信账本，也没有数字签名或只读 ACL；能同时改写来源与 manifest 的主体仍在信任边界内。因此 `source_summary_sha256` 不是批准、签名、准确率证明或 A～E 发布授权，生产重评分不得把 M81 当作可信输入根。

## 验证结果

- 显式 M78 重建得到 3 段视频、15,868 帧、546 个候选事件和 2,366 条指标实例；
- 两份 Draft 2020-12 Schema 均通过自校验，生成的 manifest 与三份 video payload 均通过实例校验；
- `tests.test_scoring_visual_observer` 运行 7 项并全部通过，覆盖缺失 `--runs-root`、M78 summary 精确绑定、重算 manifest 后替换 summary 哈希、payload 内容篡改以及 grade/threshold 安全边界。
