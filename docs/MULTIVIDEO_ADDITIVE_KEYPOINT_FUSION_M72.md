# M72 三视频同拓扑缺失关键点加法融合

## 结论

M72 在不覆盖 M71 任何有效关键点的前提下，把同一 Halpe26 拓扑的 RTMPose-L 有效缺失点逐点补入，确实让当前三视频又有 2 个指标实例从 unavailable 变为 measured。该增益只发生在第一段视频的 FS01-M02 与 FS02-M02；第二段视频补入了 31 个点但没有完成任何指标，第三段没有可补点。

固定同一候选事件边界、Track、时间戳、特征函数和质量门禁后，2,366 个指标×事件实例的结果为：

- M68：2,329 个特征向量完整，2,320 个门禁后可测；
- M71：2,344 个特征向量完整，2,333 个门禁后可测；
- M72：2,346 个特征向量完整，2,335 个门禁后可测；
- M72 相对 M71 新增 2 个完整特征实例和 2 个门禁后可测实例，回归与丢失均为 0；
- 18 个 measurement hard fail 不变，非 hard-fail operational 残差从 15 降为 13。

这些数字只表示固定边界上的 Pose 可观测性，不是人工关键点误差、事件准确率、等级准确率或生产发布结论。

## 融合合同

融合版本为 `same-topology-missing-keypoint-addition-v1.0.0`。输入必须满足：

- baseline 与 candidate 的视频、帧、人物、keypoint topology/order 完全相同；
- detection、Track、bbox、timestamp 和其他非 Pose 字段完全相同；
- 只允许处理注册表所需的左右肩、髋、膝、踝，以及 Halpe26 左右 big toe、small toe、heel；
- baseline 已有效点的坐标、置信度和 valid 状态必须逐字节保持；
- 只有 baseline 无效且 candidate 有效时才允许填补；
- 决策不得读取 feature value、event outcome、grade 或 threshold。

真实结果为 46 个 changed frames、79 个新增有效关节观测，`baseline_valid_coordinates_overwritten=0`。逐视频为：

- `3ae77...`：25 帧、48 点，恢复 2 个 feature/operational 实例；
- `850cb...`：21 帧、31 点，恢复 0；
- `8d775...`：0 帧、0 点，恢复 0。

79 个新增点只完成 2 个指标实例，直接说明有效点数量不能代替关键点准确率，也不能直接代表评分覆盖率。

## 剩余 13 条不可测实例

M72 没有为了追求 100% 覆盖而放宽门禁。剩余 13 条 non-hard-fail operational residual 为：

- 9 条 `event_observation_coverage_below_feature_contract`：事件内仍缺足够的髋、膝、踝、肩或 fine-foot 连续观测；
- 2 条 `video_start_boundary_censored_observation`：事件从视频起点开始，缺少完整前置时序；
- 2 条 `required_pose_phase_proxy_not_observed`：FS09-M05 的必要阶段代理没有被实际观测。

它们必须继续输出 unavailable。降低 valid fraction、跨事件借帧、用 0 填缺失或伪造阶段时间都违反当前契约。

## 动态证据

只有第一段视频产生指标级恢复，因此只发布一段真实变化视频：

- `reports/measurement-recovery-m72/additive-keypoint-fusion-v1/3ae77ee3271d67de171585a5c39ddd69/m71-vs-additive-keypoint-fusion-changed-browser.mp4`；
- H.264，76 帧，2.535091 秒；
- SHA256：`FD2D75...B151C097`；
- 浏览器实际解码 `readyState=4`、媒体错误为空。

机器汇总为 `reports/measurement-recovery-m72/summary/report.json`。视频 2 虽有补点但没有完成指标，视频 3 没有变化，因此不生成伪恢复视频。

## 安全结论与下一步

M72 是实验性可观测性投影，不是生产融合策略。它没有修改生产默认、自动 fallback、事件边界、特征公式、质量门禁、F0～F4、grade 或 threshold。13 项仍为 F2；没有人工事件、人工校正关键点、多教练标定和独立测试时，输出继续是 `calibration_required` 或 `unavailable`。

下一步最有价值的工作是用现有盲标工具对 79 个新增关节观测做双人独立标注与裁决，按视角计算每个关节的 MAE/P95/PCK，并在未参与 M66～M72 策略选择的独立视频上执行预注册验证。只有这些证据通过后，才可以讨论把加法融合包装成生产可用的回退策略。

最终验证为全仓 624 tests OK（16 个可选依赖跳过）；3 份 per-video 报告和 1 份汇总通过 Python validator、Draft 2020-12 Schema 与 source/artifact SHA 重放；主报告已内嵌可播放的视频和机器证据链接。
