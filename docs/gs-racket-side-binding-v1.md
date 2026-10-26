# GS 人工持拍侧源证据

本次修复为 GS01/GS02/GS03 的 M07-01 腕速增加条件提供有来源的测量侧。此前运动学计算器需要 `window.racket_side`，但严格 GS 标注入口没有可核验的持拍侧字段；满足连续帧条件的窗口也只能返回 `racket_side_unresolved`。现在由人工观察绑定对应动作，禁止从更快的一侧、通用挥拍事件字段或主手假设推断测量侧。

仍使用 `gs-stage-annotations-v1.0.0` 和 `gs-card-execution-v1.0.0`。旧文档兼容；没有新字段就保持未确定。特征单独版本为 `manual-racket-side-observation-v1.0.0`。所有原卡等级仍不支持自动给出。

## 输入

`actions[].racket_side_observation` 是可选对象；出现时只接受以下字段，均必填：

- `version`: `manual-racket-side-observation-v1.0.0`。
- `method`: `manual_visual_observation`。
- `action_id`、`video_sha256`、`person_track_id`: 必须对应所属动作及标注文档。
- `measurement_wrist_side`: `left` 或 `right`，仅指哪只腕用于运动学测量。
- `observed_racket_hands`: `left`、`right` 或 `both`。单手时必须等于测量侧；GS02 双反必须为 `both`，仍明确指定一只测量腕，不能称其为主手。
- `source`、`source_description`: 1–512 字符的可审阅来源和描述，不接受全空白。
- `uncertainty_ms`: 非负有限数，表示人工观察时间标签的不确定性。它不改变原 PTS，也不扩大计算窗口。
- `semantics`: `visually_observed_racket_holding_wrist_not_dominant_hand`。
- `source_frames`: 至少一帧、最多 2000 帧、按 `frame_index` 严格递增。每项只含非负整数 `frame_index`、`pts` 和正整数 `time_base_num`、`time_base_den`。

源帧必须存在于已核验的原帧快照，原 PTS 和时基逐字段相同，位于所属动作区间，且当帧选中人物一致。需要观察的腕点还必须通过现有卡片坐标及置信度门槛。双手观察要求两腕均有效。禁止添加分数、等级、通过标志、质量豁免或派生坐标。

## 输出与连续性

每个关联实例保留同一份核验证据于 `source_annotation.racket_side_observation` 和 `window.racket_side_binding`。保留全部输入字段，增加：

- `document_version`、`document_sha256`、`digest_kind: canonical_json_sorted_utf8_v1`：绑定整份输入标注文档。
- `technique_code` 和 `action_window_ms: {start_ms, end_ms}`：保留所属动作类别和原时间范围，供消费者复核源帧归属。
- `verification: source_frames_matched_to_decoder_and_selected_person`。
- `evidence_scope: sampled_frames_only`。
- `expert_adjudicated`、`dominant_hand_established`、`continuous_racket_hold_established`：全部为 `false`。
- `source_frame_bindings`: 每个原始帧引用增加 `video_sha256`、`person_track_id`、精确 PTS 换算的 `timestamp_ms`、`selection_epoch`、`camera_reference_epoch`。
- `stage_continuity_verified`: 只有证据帧与计算阶段的选人段和相机参考段相同时才为 `true`。只有此时才写入 `window.racket_side`。跨段时保留证据、腕侧仍未确定，不覆盖阶段原绑定，也不额外阻断不依赖腕侧的卡片。

记录级入口始终不声称已验证媒体文件。正式入口仍核验原视频、实际解码 PTS、三个原始输入文件及读前读后哈希；人工视觉声明始终不是专家真值，也不是连续握拍证明。

## 验证范围

`tests/test_gs_racket_side_binding.py` 包含 20 项测试。合成轨迹只测试数值和拒绝逻辑：12 帧右腕速度增加为 2 torso/s，左腕为 0；明确选左腕不会改选更快右腕。7 帧边界保留每端至少 3 个速度样本；3/5/6 帧不计算。另测双反双手要求、未知/伪造字段、精确 PTS 时基、动作/人物/视频不匹配、缺帧、置信度、跨相机段、输入不变性和旧标注兼容。

新的 `tests/fixtures/gs-racket-side-observations-20261022.json` 独立保存既有 MIT 三个视频第一动作的人工观察，未改历史 GS 窗口或报告：

- 正手原帧 38：可见右手持拍，左手分离。
- 单反原帧 36：可见右手持拍，左手在身后分离。
- 双反原帧 28：原图及相邻序列可见双臂汇向拍柄，指定右腕作为测量侧；未推断主手。

三个原视频均重新经正式入口核验。原 M07 窗口仍是 5、2、3 帧，15 个实例仍为 **1 个视觉代理分数、5 个需复核、9 个不可用**；三个腕速条件均不可用。全部条件值、状态、分数和汇总与去掉侧绑定时相同，新增真实计分卡数为 0。

干净验证以 `f430a2d42ea65e979531b26b1264950e13fbed52` 为基线，仅覆盖本次 GS 模块和新增测试/fixture。79 个实际导入的项目模块及规则数据均记录 raw SHA 和 UTF-8 去 BOM、CRLF/CR 归一 LF 后 SHA；除声明的 GS 模块外均与基线 Git 内容一致。原视频、原 JSONL、原图和报告使用明确的 `raw_file_sha256`；跨检出复用的标注 JSON 使用 `canonical_json_sorted_utf8_v1`。历史 M07 文件本轮不覆盖、不重标。

本地完整回放及模块清单位于 `.codex_tmp/gs-racket-side-validation-20261022/validation-evidence.json`，三个完整报告位于同目录 `production-replay/`。源媒体不是仓库测试依赖；可通过显式设置 `GS_RACKET_ARTIFACT_ROOT` 运行原媒体集成测试，未设置时仅跳过该项。提交内的真实原姿态 fixture 会持续验证全部 15 实例及无侧绑定对照。
