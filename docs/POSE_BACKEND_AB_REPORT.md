# RallyMate Pose Backend A/B 报告

版本：`pose-backend-ab-2026-08-13.1`  
状态：`no_candidate_promoted_ground_truth_required`  
默认 Backend：`yolo`（未切换）

> 覆盖率、置信度、swap/jump 启发式和特征分布不是准确率。没有人工关键点/事件真值，因此 MAE、P95、PCK、特征误差和正式晋级结论均不能伪造。

## 性能抽样

| Backend | 调用 P50/P95 ms | 每 ROI P50/P95 ms | warm-up allocated MiB | peak allocated MiB |
|---|---:|---:|---:|---:|
| `yolo` | 15.796 / 45.995 | 9.424 / 30.614 | 43.26 | 91.02 |
| `rtmpose-s-halpe26-256x192` | 7.845 / 17.004 | 6.709 / 8.735 | 29.95 | 44.94 |
| `rtmpose-m-halpe26-256x192` | 8.903 / 19.507 | 8.108 / 9.884 | 63.33 | 74.86 |
| `rtmpose-m-halpe26-384x288` | 9.486 / 21.172 | 8.591 / 10.721 | 63.83 | 82.81 |

抽样复用同一冻结 Player ROI，每视频请求 60 帧、实际 177 次调用/282 个原始 ROI 计数。RTMPose 当前为同帧多 ROI 顺序执行；该表不是完整 Detect + Track + Pose 端到端吞吐。

## 整段时序诊断（3 视频非加权平均）

| Backend | Pose 帧覆盖 | 主 Track | 目标点最小有效率 | swap/千帧 | jump/千帧 | jitter P50 body |
|---|---:|---:|---:|---:|---:|---:|
| `yolo` | 94.57% | 53.67% | 96.79% | 46.04 | 101.12 | 0.04830 |
| `rtmpose-s-halpe26-256x192` | 95.98% | 53.99% | 98.90% | 34.54 | 217.54 | 0.03786 |
| `rtmpose-m-halpe26-256x192` | 95.98% | 53.99% | 99.04% | 25.14 | 243.42 | 0.03510 |
| `rtmpose-m-halpe26-384x288` | 95.98% | 53.99% | 99.22% | 18.22 | 241.50 | 0.03423 |

RTMPose 总体提高有效率并降低 jitter，但 jump 候选明显增加，swap 也因模型/视频而异；最长缺失仍由冻结 Detection Track 碎片主导。混合结果不足以声称模型更准确。

## 晋级门禁

| 门禁 | 状态 | 结论 |
|---|---|---|
| hip/knee/ankle keypoint MAE/P95/PCK improves | `ground_truth_required` | 79-frame labeling manifest exists but corrected keypoints are not populated |
| six-indicator feature MAE/P95/Bias improves | `ground_truth_required` | manual corrected keypoints and event boundaries are absent |
| temporal diagnostics do not degrade | `mixed_diagnostic_not_accuracy` | jitter improves, but jump candidates rise and swap is model/video dependent |
| realtime end-to-end throughput >= 85% of YOLO | `not_evaluable_end_to_end_required` | pose-stage sample is faster, but full Detect+Track+Pose matched rerun is not yet the comparison source |
| contract, rollback and regression tests | `passed` | YOLO rollback/default preserved; Halpe26 schema and 34 tests pass |

## 决策

三个候选均为 `not_promoted`，默认保持 YOLO-Pose。阻断项是人工关键点误差、人工事件边界、特征误差和独立测试，而不是工程推理失败。没有执行默认切换、ONNX/TensorRT 导出、Canary 或删除回滚模型。

定性证据图：`reports/pose-ab-evidence.jpg`。图只用于人工审阅，不作为真值。机器报告保留逐视频时序和十项特征分布。
