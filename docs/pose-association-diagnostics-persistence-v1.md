# 按原帧保存姿态关联拒绝证据

`pose-association-frame-diagnostics-v1.0.0` 补齐新推理输出的诊断缺口。此前接受的姿态已有 `person_association`，但被拒绝的 ROI 不生成姿态对象，完整原因只留在内存。现在 `Yolo26Perception.estimate_poses` 在每次调用前清空上一快照，成功返回后复制当前 `PoseEstimator.last_association_diagnostics`；异常继续向上传递，不保留前帧结果。

流水线在写入每条 `frames.jsonl` 前，将该快照写入可选的 `pose_association` 对象。它绑定同一条记录的原帧序号、处理序号、时间戳、时间戳来源和原图宽高；若调用方另有 `source_timestamp_ms`，它与相对播放时间分别保存。既有 frame schema 允许此附加字段；旧后端没有诊断时不新增字段，不将缺失解释为关联成功。

对象保留原 policy version、逐 ROI 原人物框、track、候选框、候选选择与拒绝原因，以及跨 ROI 重复人体审计。空人物帧有自己的空 ROI 列表；不会复制上一帧的拒绝。序列化后的记录与可变的模型内存独立，不会被下一次推理改写。

被拒绝的非法候选仍可能包含 NaN 或 Infinity。诊断副本将这些值置为 JSON `null`，并在 `invalid_numeric_values` 中逐项保存 RFC 6901 JSON pointer 与 `nan`、`inf` 或 `-inf` 表示；这只改变诊断的序列化表示，不修改任何原候选、接受姿态或评分数值。其他合法数值保持原值。未知的非 JSON 对象不被静默转成字符串。

## 实际原视频与写盘验证

使用已知 USTA 开发反例的原视频，从头实际解码到原帧 11985、11986、11987，并以相同 YOLO26n-pose 权重、CPU、640 输入、0.25 confidence 和原 ROI margin 0.15 重新运行六个 ROI。原 detector/tracker 输出从冻结原记录读取，没有重新执行 detector/tracker，也没有生成新动作评分。

原视频 SHA 为 `80ba340c0f4a6d0c49d84d74e3a6ca4132345eb6d887950c74c1a6f5678d32b0`；权重 SHA 为 `eb3bb8268828aeaf515cec23a4bfafd793944a86fe9af94ba7823609c14522a9`。三张 OpenCV 解码图像的 BGR 像素摘要与上轮实际重放逐一相同。生产 `run_pipeline` 的实际 JSONL 写入器与 frame validator 执行完三帧后，验证程序明确停止在选人和评分后处理之前，因此这不是一个完整服务任务。

三条记录实际保存的姿态 track 分别为 `[2,1]`、`[1]`、`[2,1]`。五个保留姿态除已有的关联证据外，全部原字段与冻结原输出逐值相同；第 11986 帧没有为 track 2 生成替代姿态，其 `pose_target_candidate_ambiguous` 原因、三个候选框和 IoU 差值随同原帧 11986、399933 ms 一起写盘。原帧、原选人、事件、summary、视频及权重执行前后摘要不变。

实际产物在 `.codex_tmp/pose-diagnostics-persistence-20261024/actual-inference-writer/frames.jsonl`；收据为同目录上一级的 `actual-inference-writer-receipt.json`，SHA 为 `8bc7c0f05ad74590f41dfbd260c21647f2590387d803db9345e695d40177ab88`。收据绑定纯 `b060521` 基础与本补丁三个源文件、97 个实际导入的项目源码、模型和媒体摘要、三条写盘记录及验证程序摘要。已知反例仅用于开发回归，不构成新的留出集准确率或评分卡覆盖。

## 检查范围

8 项新增检查覆盖同步快照、异常清理、空帧、旧后端无证据、非有限候选的严格 JSON、绝对 PTS 与播放时间分离、冻结实际候选的拒绝，以及生产写入器的三帧契约。写入器单元检查使用假解码器和保存的候选张量；它与上述实际原视频模型运行分别记录。

连同原姿态关联、后端、流水线 registry/progress、检测和 frame validation，共 72 项检查最终通过，无剩余失败或跳过。首次隔离运行有 14 项缺少仓库外测试素材；补入原始素材副本并校验摘要后，13 项通过，另 1 项所需的独立历史 context bundle 随后补齐并通过。一次测试名拼接错误也保留日志，修正后重新执行对应项目；没有修改测试断言或生产结果来消除这些环境失败。

这项改动保存拒绝的原因，不自动恢复被拒绝姿态，不合成原始关节，也不将几何关联审计提升为真实人物身份验证。后续评分仍消费原有几何与来源门槛。
