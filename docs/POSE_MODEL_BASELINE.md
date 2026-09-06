# RallyMate YOLO-Pose 基线

文档版本：`yolo-pose-baseline-2026-08-13.1`  
生成时间：2026-08-12T16:48:50.617209+00:00  
状态：已冻结工程/时序基线；人工关键点误差仍为 `ground_truth_required`

> 本报告中的覆盖率、左右交换候选、跳点候选和模型置信度均不是准确率。特征分布是全 Track、无事件、无平滑的诊断值，不能用于 A～E 判级。

## 1. 模型与环境

- Detect：`models/yolo26n.pt`，SHA-256 `9B09CC8BF347F0FC8A5F7657480587F25DB09B34BF33B0652110FB03A8AD4FEF`
- Pose：`models/yolo26n-pose.pt`，SHA-256 `EB3BB8268828AEAF515CEC23A4BFAFD793944A86FE9AF94BA7823609C14522A9`
- Python 3.10.18 / Torch 2.11.0+cu128 / Ultralytics 8.4.21 / OpenCV 4.13.0
- GPU：NVIDIA GeForce RTX 5070 Ti
- 推理参数：Detect 960/conf 0.15；Pose ROI 640/conf 0.25；最多 2 人；frame stride 1。

## 2. 固定视频与历史端到端结果

| 角色 | 视频 | 帧数 | 历史端到端 FPS | Pose 帧覆盖 | 主 Pose Track | Pose Track 数 | 最大框 Track 变化 |
|---|---|---:|---:|---:|---:|---:|---:|
| short | `3ae77ee3271d67de171585a5c39ddd69` | 1441 | 26.163 | 96.46% | 70.30% | 15 | 24 |
| difficult | `850cb0006b406c7176eeda8d711cd065` | 2911 | 19.823 | 88.94% | 48.61% | 26 | 69 |
| long | `8d7754d0de6d315674013d5b69a0b6ba` | 11516 | 23.409 | 98.30% | 42.09% | 33 | 59 |

历史端到端 FPS 来自已有整段运行产物；本次未重跑整段推理。最大框 Track 变化只表示逐帧最大框策略的原始 Track 改变，不等于有人工身份真值的 ID Switch。

## 3. 本次 Pose 性能抽样

- 抽样：每视频请求 60 帧；5 次 warm-up；复用当前 Player ROI 与最多 2 人批次。
- 当前组合 Perception 构造时间：1.523 s（Detect + Pose，尚未拆分 Backend；GPU 权重可能惰性物化）。
- Pose 调用 P50/P95：15.796 / 45.995 ms。
- 每 ROI P50/P95：9.424 / 30.614 ms。
- CUDA allocated 构造后 / warm-up 后 / peak：0.000 / 43.258 / 91.025 MiB。
- CUDA reserved 构造后 / warm-up 后 / peak：0.000 / 90.000 / 162.000 MiB。

这些显存值是当前 Python 进程的 PyTorch allocator 指标，不是 `nvidia-smi` 的整卡占用。

| 视频 | 调用样本 | ROI | 调用 P50 | 调用 P95 | 每 ROI P50 | 每 ROI P95 |
|---|---:|---:|---:|---:|---:|---:|
| `3ae77ee3271d67de171585a5c39ddd69` | 57 | 59 | 14.489 | 52.545 | 14.219 | 50.530 |
| `850cb0006b406c7176eeda8d711cd065` | 60 | 116 | 15.830 | 22.079 | 7.947 | 11.058 |
| `8d7754d0de6d315674013d5b69a0b6ba` | 60 | 107 | 16.172 | 21.702 | 8.565 | 15.337 |

## 4. 关键点有效率与时序诊断

| 视频 | 肩最小有效率 | 髋最小有效率 | 膝最小有效率 | 踝最小有效率 | swap 候选 | jump 候选帧 | 主 Track 最长全视频缺失 |
|---|---:|---:|---:|---:|---:|---:|---:|
| `3ae77ee3271d67de171585a5c39ddd69` | 99.70% | 99.61% | 98.91% | 98.03% | 16 | 124 | 399 帧 |
| `850cb0006b406c7176eeda8d711cd065` | 94.91% | 93.43% | 94.98% | 92.65% | 54 | 127 | 891 帧 |
| `8d7754d0de6d315674013d5b69a0b6ba` | 99.69% | 99.88% | 99.83% | 99.71% | 408 | 442 | 4324 帧 |

swap 采用相邻帧左右匹配代价启发式；jump 采用 body-scale 位移与 robust speed MAD 启发式。原始数值、参数、逐点 P50/P95、jitter residual 和最长缺失均在 `reports/pose-model-baseline.json`。这些结果用于新旧模型相对对照，不是真值准确率。

## 5. 六指标基础特征诊断

以下是主 Pose Track 全区间、归一化坐标、无事件、无平滑的 P50（括号内为有效率）。`stability_duration_ms` 因缺少事件边界和经真值验证的 stability envelope 保持 unavailable。

| 特征 | short | difficult | long |
|---|---:|---:|---:|
| `hip_center_y_body` | 3.306 (99.6%) | 13.255 (92.9%) | 37.776 (99.9%) |
| `body_center_speed_body_s` | 1.362 (99.4%) | 2.162 (90.9%) | 3.984 (99.0%) |
| `left_knee_flexion_deg` | 44.457 (98.1%) | 8.951 (89.6%) | 12.405 (99.6%) |
| `right_knee_flexion_deg` | 33.186 (97.9%) | 11.303 (93.9%) | 9.568 (99.8%) |
| `torso_lean_deg` | 7.399 (99.6%) | 4.016 (92.0%) | -1.295 (99.6%) |
| `stance_width_body` | 1.532 (98.0%) | 1.291 (91.7%) | 1.794 (99.7%) |
| `hip_center_relative_to_ankle_support` | 0.118 (97.9%) | 0.148 (88.7%) | 1.353 (99.6%) |
| `body_center_deceleration_body_s2` | -0.277 (99.2%) | -1.203 (90.4%) | -0.707 (98.5%) |
| `shoulder_hip_angular_velocity` | -1.900 (99.4%) | 2.516 (90.9%) | 4.572 (99.0%) |
| `stability_duration_ms` | unavailable | unavailable | unavailable |

## 6. 人工真值状态

- 当前没有肩、髋、膝、踝人工校正点，因此关键点 MAE/P95、PCK 和事件关键帧误差均为 `ground_truth_required`，未写 0。
- 已生成待标注清单：`data/annotations/pose-baseline-labeling-manifest.json`，共 79 个候选帧。
- 清单覆盖低置信、跳点候选和时间均匀样本；缺失点必须使用 `null + visibility/reason`，禁止 `[0,0]` 填充。
- 未来模型不能用自身预测或另一模型未经人工确认的输出作为真值。

## 7. 当前阻断项

1. 尚无人工关键点真值，不能决定 RTMPose 是否降低网球关键点误差。
2. 尚无稳定主球员身份层，最长 Pose Track 不能代表业务身份准确。
3. 尚无 FS01/FS02/FS09 事件边界，当前特征分布不是事件内评分特征。
4. 当前 Pose 与 Detect 共处 `Yolo26Perception`，加载时间和显存还不能精确拆分到单独 Backend。
5. 当前环境未安装 ONNX Runtime/TensorRT/MMPose；下一里程碑先完成 Backend 接口与 YOLO 适配，再隔离 RTMPose runtime。

## 8. 复现命令

```powershell
$env:PYTHONPATH = "$PWD\src"
.\.venv\Scripts\python.exe .\scripts\build_pose_baseline.py --root . --benchmark
.\scripts\run_tests.ps1
```

机器报告保留模型/视频/frames/summary 哈希、所有分布和性能采样细节。
