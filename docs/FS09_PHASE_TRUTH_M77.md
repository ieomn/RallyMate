# M77 / M86 / M87 / M88 FS09 两任务人工真值试点

> **HARD STOP — 当前禁止开始真人标注。** 当前公开包只是 `technical_handoff_verified_external_protocol_required` 技术验证包。仓库尚无独立外部协议回执、可信锚或 authorized-bundle 合同；页面会禁用保存、导入和导出，中央 intake 也会拒绝非空导出。后来取得回执不能原地解锁该包，必须另行实现并生成一个版本化的 authorized bundle。

本试点只评测两个 FS09-M05 事件的人工边界、四个可见阶段以及人工边界条件下的六项特征变化。它不是教练评分真值，不生成 A～E、标定阈值或 F2→F3/F4 晋级。

## 1. 当前真实状态

- 私有权威包：`data/annotations/fs09-phase-truth-m77-v1/`。包含密封候选、绝对来源谱系和编译证据，只能由中央操作员保管。
- 公开技术盲包：`data/annotations/fs09-phase-truth-m77-blind-handoff-v1/`。可移动、可逐字节验证，但当前不可执行人工标注。
- 范围：1 个固定机位视频、2 个 FS09-M05 任务；每段媒体 5 秒、24 FPS、H.264、无音频、无 Pose/候选叠加。
- 真值仍为 0 annotation、0 adjudication、0 compiled event；所有空白 Event/phase/feature 指标保持 `null`。
- 分析计划在零标签状态下以内容哈希绑定任务、媒体、工作台、缺失值口径和安全边界；它是仓库内描述性计划，不是外部登记、真人签名或生产接受协议。

公开包不含私有 manifest、`tasks.jsonl`、`sealed-event-candidates.jsonl`、compiled 目录或源路径。它公开一个预冻结的、候选选择得到的粗粒度目标提示点，以帮助人在短片内识别目标动作；该提示点不是候选边界或阶段，也不约束人工 start/end/phase。公开窗口不能按固定 padding 公式反推出精确候选边界。

这不是全局保密机制：提示点与 candidate-contract SHA 可能在已知仓库/模型输出上形成查找线索。未来 A/B/C 必须在隔离设备上只接触公开 authorized bundle，禁止访问仓库、`reports/`、runtime events、其他 annotation pack、私有候选或模型输出。SHA 是完整性承诺，不提供对已知源数据字典查询的机密性。

## 2. 当前只允许技术验证

把公开技术包复制到任意目录后，在该目录运行自带服务器：

```powershell
Set-Location <public-handoff-directory>
python serve_fs09_phase_blind_handoff.py --directory . --bind 127.0.0.1 --port 8765 --validate-only
```

去掉 `--validate-only` 可仅为技术 QA 打开 `http://127.0.0.1:8765/review.html`。服务器必须限制在 loopback，先验证精确文件树、计划语义、manifest、SHA-256 和内容根，再从内存快照服务白名单；目录列表、私有文件、路径穿越与非白名单请求必须失败。不要使用 `python -m http.server`，也不要把私有权威包或 intake session 作为 HTTP 根。

技术 QA 可检查两段视频完整 `seekable`、±1 frame（约 42 ms）、±100 ms、local/source 时间映射和粗粒度提示点跳转。当前授权门禁为 false，因此任何保存、导入、导出或 intake 成功都应视为缺陷。

## 3. 未来授权合同必须具备的内容

在任何首次作答或保存之前，独立负责人必须提供调用方不可选择的可信回执/签名锚。未来的新 authorized bundle/version 至少要绑定：

- technical handoff manifest 的原始 SHA、bundle ID 与 analysis-plan SHA；
- 外部 registry/authority ID、独立 reviewer 身份与不可回填的登记时间；
- 登记时间严格早于每条人工 decision；
- A/B/C 角色隔离、仅公开包访问和禁止已知源查找的操作协议；
- 全量报告、缺失/不可观测处理和明确禁止的生产结论。

当前 `examples/fs09-phase-diagnostic-protocol.template.json` 只有占位符，故意不可执行。它既不是 receipt，也不能授权现有 technical bundle。

## 4. 未来人工观察口径

- **事件起点**：身体整体从移动/接近明确转入制动或恢复控制的第一帧。
- **峰速**：可见身体中心平移最快、随后开始减速的时刻；不得用挥拍或单肢速度代替。
- **减速峰**：身体中心速度下降最明显、制动最强的可见时刻；不等于脚接触真值。
- **重新稳定开始**：强制动后，躯干/骨盆摆动开始持续减小并转向恢复平衡的第一帧。
- **稳定控制开始**：至少连续 3 帧可见躯干与骨盆已受控，且没有新的大幅纠正步或晃动的第一帧。
- **事件终点**：稳定控制已经建立后的第一帧；不足以判断时使用 `unobservable` 并写原因。

工作台不预选正例或置信度。非 `observed` 阶段必须留空时间并写原因，不能用 0 代替缺失。“稳定控制”只是可见身体控制状态，不是真实双支撑、足底接触、地反力、足压或负荷转移真值。

每条 annotation revision 是无密钥内容摘要；reviewer 裁决必须绑定 A/B 精确 revision，输入改变就必须重新裁决。浏览器记录的 decision/export 时间来自可修改的客户端时钟，只能证明未篡改工作台内的自洽关系，不是签名或可信时间戳。

## 5. 未来 intake 与评测边界

当前中央 intake 对 technical-only handoff 必须 fail closed。只有未来实现独立 authorized contract 后，才可恢复 A/B 各 2 条 annotation、C 共 2 条 accepted adjudication 的正向链；不得手工拼 CSV 或覆盖空白包。

未来成功的 intake session 仍是**私有敏感证据**：它包含密封候选、绝对源路径、原始人工导出/匿名 ID、manual truth 和视频。它不得分发、不得作为 HTTP 根、不得写入评测输出。必须与原私有 source pack 的原路径和原字节一起保管；只归档 session 不足以重放来源验证。

评测只允许输出：

1. Segment IoU、start/end Boundary MAE 与 Bias；
2. 四个可见阶段的 timestamp MAE/Bias 与可观测性；
3. 同一模型 Pose 下，换成人工 event interval 后六项 FS09-M05 required feature 的差异。

两个预选任务来自同一视频/会话/球员上下文，不能估计 Event precision/recall/F1、总体发生率或泛化性能，也不构成 train/test 或独立 holdout。即使未来完成，状态仍须保留外部接受协议要求，`thresholds=null`、runtime/grade/maturity/production 全部不变。
