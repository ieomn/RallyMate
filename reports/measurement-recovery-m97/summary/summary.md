# M97 RTMPose-X 384 operational 恢复验证

状态：`experimental_x_extension_regression_free_to_m70_but_does_not_preserve_m71_recovery`

本次只重放 M70/M71 已冻结的 3 个开发视频、258 个残差帧，并逐帧保持相同的 source frame、timestamp、track、ROI margin、最小 ROI 尺寸和 `analysis`/flip-test 配置。路由仍是 required-joint validity 的严格 superset/no-regression 规则。

结果：

- X 在 258 帧中返回 254 个姿态；严格路由选中 56 帧。
- 相对 M68：feature 恢复 13、回归 0，feature-complete 为 2342/2366；operational 恢复 11、回归 0，operational-measured 为 2331/2366。
- 相对 M70：feature 与 operational 都新增 4 个恢复项，丢失 0 个 M70 恢复项。
- 相对 M71：feature 与 operational 都新增 1 个不同恢复项，但丢失 3 个 M71 恢复项；净计数少 2，低于 M71 的 2344/2333。
- 因此 X 不能取代 M71 的本开发集恢复结论，也不构成候选晋级依据。

速度诊断：

- 255 次 GPU 同步 `PoseEstimator` 调用的 P50 为 21.1203 ms，P95 为 25.57492 ms；三次独立进程的首次调用产生约 297–300 ms 冷启动离群值。
- 有 3 帧没有唯一可用的 source detection，另有 1 次调用因 ROI 未被 estimator 接受而快速返回空姿态；因此耗时样本与返回姿态数分别为 255 和 254。
- 本次是 X 的 `analysis`/flip-test 路径，没有在同一轮重跑 M/L，因此这些耗时不能直接与 M/L 比较。M96 的 45 个同帧 `realtime`/no-flip 诊断仍是 M/L/X 速度横向对照，两个实验的耗时口径不可混用。

尚未完成：人工 Halpe26 关键点真值、逐视角特征误差、预注册的独立视频发布验证、候选晋级和生产默认模型变更。报告不声称准确率提升，未打开或重新哈希 c235 holdout，默认模型仍为 `rtmpose-m-halpe26-online`。
