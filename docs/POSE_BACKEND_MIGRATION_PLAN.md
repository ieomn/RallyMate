# RallyMate Pose Backend 迁移与评分闭环计划

文档版本：`0.1.0`  
状态：执行中  
基线日期：2026-08-13  
项目根目录：`<仓库根目录>`  
关联计划：[`SCORING_FEASIBILITY_PLAN.md`](../SCORING_FEASIBILITY_PLAN.md)

## 1. 目标与执行顺序

本计划把 Pose 模型迁移放在六个 Pose-only 指标的评分闭环之前，最终链路为：

```text
视频观测
  -> 稳定主球员 Track
  -> 可替换 Pose Backend
  -> Pose 时序质量
  -> FS01 / FS02 / FS09 事件
  -> 版本化运动学特征
  -> 质量与视角门禁
  -> 教练标定接口
  -> A-E / calibration_required / unavailable
  -> 证据与反馈
```

不会因为 RTMPose 能输出关键点就切换默认模型。只有候选模型在 RallyMate 固定网球测试集的关键点误差、时序稳定、特征误差和性能门禁全部通过，才能晋级；否则保留 YOLO-Pose 默认并输出明确原因。

## 2. 范围和不变量

### 2.1 保留

- 现有 YOLO Detect、Player ROI、对象 Track、任务队列、API、Worker 和产物目录。
- `yolo26n-pose.pt` 及旧 `coco17` 输出读取能力。
- 现有历史任务、28 项测试和 `supported / partial / unsupported` 静态审计。
- 旧路径通过 `RALLYMATE_POSE_BACKEND=yolo` 可回滚。

### 2.2 不做

- 不重写 Ball、Racket、Court、前端、SQLite 队列或部署基础设施。
- 不将 RTMW/WholeBody-133 设为默认候选。
- 不把 26 点结果标成 `coco17`，不自行创造 RallyMate J 编号。
- 不用官方 COCO/Body8 指标替代网球测试集结果。
- 不伪造关键点真值、教练标注或 A～E 阈值。
- 不先训练 Keypoint Refinement；先做误差分解。

## 3. 当前事实基线

- 当前 Pose 被直接封装在 `Yolo26Perception` 内；Worker 和 Pipeline 间接依赖该组合对象，没有独立 `PoseBackend`。
- `estimate_poses()` 对 Player ROI 调 Ultralytics Pose，选 ROI 内最高置信人体，并直接构造固定 17 点输出。
- `frame-observation.schema.json` 把 `keypoint_format` 固定为 `coco17` 且关键点数量固定为 17。
- 当前环境：Python 3.10.18、PyTorch 2.11.0+cu128、Ultralytics 8.4.21、OpenCV 4.13.0、RTX 5070 Ti 16 GiB。
- 当前生产环境未安装 `onnxruntime`、TensorRT、MMPose、MMCV 或 MMEngine；迁移必须隔离依赖，不能破坏现有 yolo 环境。
- Git 仓库尚无提交，所有内容未跟踪。每个里程碑给出提交建议；在没有可审查初始提交前不自动把用户全部现有资产提交为一个巨型 commit。

## 4. 官方候选模型登记

以下名称、输入尺寸和 checkpoint 均来自 OpenMMLab MMPose 官方 `rtmpose_body8-halpe26.md`，不是凭记忆填写：

| Candidate ID | 官方 Config | 输入 | 官方 checkpoint | 当前状态 |
|---|---|---:|---|---|
| `rtmpose-s-halpe26-256x192` | `rtmpose-s_8xb1024-700e_body8-halpe26-256x192.py` | 256×192 | `rtmpose-s_simcc-body7_pt-body7-halpe26_700e-256x192-7f134165_20230605.pth` | 待取得/导出 |
| `rtmpose-m-halpe26-256x192` | `rtmpose-m_8xb512-700e_body8-halpe26-256x192.py` | 256×192 | `rtmpose-m_simcc-body7_pt-body7-halpe26_700e-256x192-4d3e73dd_20230605.pth` | 待取得/导出 |
| `rtmpose-m-halpe26-384x288` | `rtmpose-m_8xb512-700e_body8-halpe26-384x288.py` | 384×288 | `rtmpose-m_simcc-body7_pt-body7-halpe26_700e-384x288-89e6428b_20230605.pth` | 待取得/导出 |

官方来源：

- Model zoo: <https://github.com/open-mmlab/mmpose/blob/main/configs/body_2d_keypoint/rtmpose/body8/rtmpose_body8-halpe26.md>
- Inference API: <https://github.com/open-mmlab/mmpose/blob/main/docs/en/user_guides/inference.md>
- Halpe dataset metainfo: <https://github.com/open-mmlab/mmpose/blob/main/configs/_base_/datasets/halpe.py>

官方 Halpe 元数据确认 0～16 与 COCO-17 身体点同名同序，17～25 依次为 `head, neck, hip, left/right_big_toe, left/right_small_toe, left/right_heel`。仓库中只保存经来源校验的 26 点元数据单一副本；转换器读取该副本，其他模块不得手工复制另一套顺序。

## 5. 统一 Pose Backend 契约

计划目录：

```text
src/rallymate_vision/pose/
  __init__.py
  base.py
  metadata.py
  adapters.py
  registry.py
  yolo_backend.py
  rtmpose_backend.py
  data/
    keypoint_schemas.json
```

核心接口：

```python
class PoseBackend(Protocol):
    def load(self) -> None: ...
    def infer(self, rois, *, timestamps_ms=None) -> list[PosePrediction]: ...
    def metadata(self) -> PoseModelMetadata: ...
```

统一中间表示至少包含：ROI 索引、原始关键点索引/名称、x/y、confidence、visibility/missing、`keypoint_format`、Schema 版本、模型名/版本/hash、backend、runtime、input size 和 inference profile。

边界规则：

- Backend 输入是现有 Player ROI，不重复做人检测。
- Backend 输出先留在 ROI/模型坐标，Adapter 统一恢复原图坐标并校验有限值、边界和 ROI 对应关系。
- 原图越界预测不能靠 clamp 静默掩盖；先记录 invalid/out-of-frame，再由契约决定是否输出兼容视图。
- `halpe26` 原生视图与 `coco17` 兼容投影视图并存。新增足点 `downstream_joint_id=null`，直到业务 J 注册表正式分配编号。

## 6. 配置和回滚

新增环境变量：

```text
RALLYMATE_POSE_BACKEND=yolo|rtmpose
RALLYMATE_POSE_MODEL=<path>
RALLYMATE_POSE_CONFIG=<path or metadata>
RALLYMATE_POSE_PROFILE=realtime|analysis
RALLYMATE_POSE_RUNTIME=onnxruntime|tensorrt|pytorch
```

兼容默认值必须等价于当前行为：

```text
backend=yolo
model=models/yolo26n-pose.pt
profile=realtime
runtime=pytorch
keypoint_format=coco17
```

Worker 启动后在 Summary 写入实际 backend、runtime、profile、input size、Schema 版本、模型文件 SHA-256 和加载时间。改变 Backend/模型需要重启 Worker。回滚不删除新文件，只切换配置到 `yolo` 并重启。

## 7. 契约兼容策略

1. 旧 `frames.jsonl` 的 `coco17` 记录保持可读。
2. 新原生 `halpe26` 使用独立 format 和 Schema 版本。
3. 对外业务默认仍可请求/读取 `coco17` 兼容投影；原生输出单独标识。
4. 必须同步校验 Schema、Python validator、render、Summary、capabilities、readiness、model manifest、fixtures 和报告。
5. 不兼容字段语义变化必须升级版本，不能只改变数组长度。

## 8. 固定测试集和基线协议

首批使用仓库已有、已运行的视频：

| 角色 | 视频 | 选择理由 |
|---|---|---|
| short | `FULL-TEST/3ae77ee3271d67de171585a5c39ddd69.mp4` | 1,441 帧，短样本，快速回归 |
| difficult | `FULL-TEST/850cb0006b406c7176eeda8d711cd065.mp4` | 2,911 帧，已有 Pose 覆盖相对最低 |
| long | `FULL-TEST/8d7754d0de6d315674013d5b69a0b6ba.mp4` | 11,516 帧，主 Pose Track 碎片问题明显 |

`docs/POSE_MODEL_BASELINE.md` 和机器可读报告记录：

- 视频/模型哈希、环境、命令、推理参数和采样方法；
- Pose 调用 P50/P95、加载时间、峰值 CUDA allocated/reserved；
- 端到端 FPS（明确是历史整段实测还是本次重跑）；
- 每点有效帧比例、主 Pose Track fraction；
- 左右交换候选、归一化跳变、时序抖动、最长缺失；
- 人工误差若无真值则 `ground_truth_required` 并生成待标注 manifest；
- 十个基础特征的原始诊断分布/有效率；事件或 stability envelope 缺失的特征必须明确 unavailable。

覆盖率、启发式 swap/jump 诊断和模型置信度均不称为准确率。

## 9. A/B 评测与晋级门禁

### 9.1 四层评测

1. 关键点：归一化误差、PCK、MAE/P95，重点肩/髋/膝/踝，按视角/远近/遮挡分组。
2. 时序：抖动、左右交换、最长缺失、Track 内有效率和事件关键帧误差。
3. 特征：十个基础特征相对人工校正点的 MAE/P95/Bias/有效率。
4. 性能：加载、每 ROI、batch、Pose P50/P95、端到端 FPS、任务 P50/P95 和显存。

### 9.2 预注册相对门禁

- 髋/膝/踝总体误差相对 YOLO 明确下降；有真值前状态为 `ground_truth_required`。
- 六指标核心特征误差总体下降；有真值前状态为 `ground_truth_required`。
- swap/jump/jitter 不恶化，关键点有效率不明显下降。
- realtime 端到端吞吐不低于 YOLO 基线的 85%。
- P95 和显存在当前单 GPU 可接受范围；绝对上限须在比较前登记，当前为 `pending_protocol_registration`。
- 所有测试通过，配置回滚成功。

任何候选不满足全部门禁时不切默认。官方 PCK/AUC 仅作为候选背景，不能替代 RallyMate 门禁。

## 10. 评分闭环衔接

模型迁移完成后继续六个指标：`FS01-M02`、`FS01-M05`、`FS02-M02`、`FS09-M03`、`FS09-M04`、`FS09-M05`。F0～F4 定义、事件/特征/真值/标定契约沿用 `SCORING_FEASIBILITY_PLAN.md`。

无教练真值时最多 F2，评分统一输出 `calibration_required` 或 `unavailable`。Pose Backend 的变化必须出现在每个 feature/score 的 provenance 中，避免不同模型结果被当作同一评分版本。

## 11. 里程碑

| ID | 交付 | 状态 | 退出条件 |
|---|---|---|---|
| P0 | 迁移计划与协议冻结 | completed | 本文、官方候选登记、范围/门禁明确 |
| P1 | YOLO-Pose baseline | completed | `POSE_MODEL_BASELINE.md`、机器报告、待标注 manifest、29/29 测试通过 |
| P2 | Backend 接口 + YOLO 适配 | completed | 默认输出与旧路径逐字段回归等价，单配置回滚，33/33 测试 |
| P3 | halpe26 契约 + RTMPose runtime | completed | 三候选隔离 GPU 推理通过；坐标/批次/元数据/契约测试通过 |
| P4 | RallyMate A/B 比较 | completed | 四层报告、manifest、可视化和逐门禁结论 |
| P5 | 默认/不晋级决策 | completed | 三候选不晋级；默认/回滚保持 YOLO，未触发导出与 Canary |
| P6 | 主球员与 Pose 时序 | completed | 稳定语义身份、来源 Track 追溯和完整诊断 |
| P7 | FS01/02/09 事件与特征 | completed | 三事件规则候选、十特征、六指标均达工程 F2 |
| P8 | 误差、标定、报告 | completed_with_external_truth_blockers | 评测/标定接口、无真值安全状态、HTML 与追溯完成 |
| P9 | 最终验收 | completed_for_available_inputs | 工程验收通过；真实 F3/F4 由真值和独立测试阻断 |

每个里程碑运行现有测试与新增测试，更新本文状态、实测结果、风险和下一步。仓库未有初始 commit 期间给出提交建议；形成可审查代码基线后再创建小而可回滚的提交。

### 11.1 P1 实测结论

- 冻结视频：short 1,441 帧、difficult 2,911 帧、long 11,516 帧；历史整段端到端 FPS 分别为 26.163、19.823、23.409。
- 当前 YOLO Pose 抽样调用 P50/P95 为 15.796/45.995 ms，每 ROI P50/P95 为 9.424/30.614 ms。
- PyTorch allocator 在 Pose warm-up 后 allocated/reserved 为 43.258/90 MiB，抽样峰值为 91.025/162 MiB；这不是整卡显存。
- 三段视频主 Pose Track 全视频覆盖分别为 70.30%、48.61%、42.09%，证明“最长 Track”不足以作为稳定主球员身份层。
- 启发式 swap 候选分别为 16/54/408；去重 jump 候选帧为 124/127/442。它们仅用于后续模型相对对照，不是真值准确率。
- 已生成 79 帧人工关键点待标注清单；人工 MAE/P95/PCK 仍为 `ground_truth_required`。
- 十个基础特征已有全 Track、无事件、无平滑诊断分布；`stability_duration_ms` 因事件边界与验证过的稳定包络缺失而保持 unavailable。
- P1 修改后编译通过，现有与新增测试合计 29/29 通过。

P1 产物：`docs/POSE_MODEL_BASELINE.md`、`reports/pose-model-baseline.json`、`reports/pose-model-baseline-benchmark.json`、`data/annotations/pose-baseline-labeling-manifest.json`。下一步 P2 只拆分 Backend 契约并用 YOLO adapter 保持默认路径等价，不改变评分、Tracking 或事件行为。

### 11.2 P2 实测结论

- 新增 `PoseBackend`、`PoseBackendOutput`、`PoseModelMetadata`、统一 ROI `PoseEstimator`、Backend registry 和独立 `YoloPoseBackend`。
- COCO-17 与官方 Halpe26 点序收敛到单一机器可读注册表；Halpe26 新增 9 点的 RallyMate J 映射保持 `null`，没有擅自编号。
- 服务/请求支持 `pose_backend/runtime/profile/config`，缺省仍为 `yolo/pytorch/realtime`；只改 `RALLYMATE_POSE_BACKEND=yolo` 即可回滚。
- 新路径在冻结 short 视频前 3 帧与旧 `runs/full-test` 产物比较：detections 与 poses 均逐字段完全一致，关键点最大绝对差值 0。
- 新路径端到端 smoke：3 帧、8 detections、3 poses，`validate_run_artifacts` passed；Summary 含 backend、runtime、profile、模型 SHA-256、输入尺寸和 Schema 版本。
- 编译通过，现有与新增测试合计 33/33 通过。

P2 保留 `Yolo26Perception` 作为 Detect + Pose facade 以兼容调用方，但 Pose 加载和 ROI 推理已委托给独立 Backend。P3 将实现 RTMPose runtime；未完成前选择 `rtmpose` 会显式失败，不会静默退回或伪装成 YOLO。

### 11.3 P3 实测结论

- 建立隔离 Windows runtime：MMPose 1.3.2、MMDect 3.3.0、MMCV-lite 2.1.0、MMEngine 0.10.7、NumPy 1.26.4，复用 CUDA PyTorch 2.11.0；yolo 环境仍为 NumPy 2.2.6。
- 三份 checkpoint 均来自官方 OpenMMLab URL，文件大小和 SHA-256 已登记在 `models/rtmpose/model-candidates.json`。
- 新增原生 `halpe26` 契约与 frame schema `1.1.0`；旧 `coco17`/schema `1.0.0` 继续可读。Halpe 17～25 点的 J 映射保持 `null`。
- RTMPose-s 完整 Pipeline smoke：3 帧、8 detections、3 poses，Halpe26 输出和全部运行产物校验通过。
- 同一冻结集抽样（每视频请求 12 帧、35 个实际调用、56 个原始 ROI 计数）三候选均通过 26 点 shape/finite smoke：
  - s-256：调用 P50/P95 8.822/16.187 ms，warm-up allocated 29.95 MiB，peak 44.94 MiB；
  - m-256：9.014/19.294 ms，63.33 MiB，peak 74.86 MiB；
  - m-384：9.347/18.696 ms，63.83 MiB，peak 82.81 MiB。
- 当前实现对同帧多个 ROI 顺序执行，报告已标为 `sequential_ROIs_within_each_frame_v0`；不能把该抽样延迟直接称作整段端到端吞吐。
- 官方模型 zoo 指标只作为候选背景；RallyMate 人工关键点/事件/特征真值仍缺失，三个候选均未晋级。
- 编译通过，现有与新增测试合计 34/34 通过。

P3 产物：`reports/rtmpose-candidate-smoke.json`、`models/rtmpose/model-candidates.json`、`runtime/rtmpose/requirements-lock.txt` 和可复现环境脚本。P4 将在相同 ROI/视频协议上输出 YOLO 与三候选的四层对照，并把无真值门禁明确保留为 `ground_truth_required`。

### 11.4 P4/P5 实测结论

- 三候选在冻结 Detection ROI/Track 上完成整段回放：每候选 15,868 帧，覆盖 short/difficult/long 三视频；逐帧 Halpe26 契约校验通过。
- 同协议每视频请求 60 帧（177 次实际调用/282 个原始 ROI 计数）Pose-stage P50/P95：YOLO 15.796/45.995 ms；RTMPose s-256 7.845/17.004、m-256 8.903/19.507、m-384 9.486/21.172 ms。
- 三视频非加权诊断显示 RTMPose 目标点有效率和 jitter 改善，但 jump 候选率明显增加，swap 候选按模型/视频混合；最长缺失没有改善，仍由冻结 Detection Track 碎片主导。
- 关键点 MAE/P95/PCK、事件边界误差和特征 MAE/P95/Bias 全部保持 `ground_truth_required`；覆盖率和定性叠图未被当作准确率。
- 逐门禁结论为 `no_candidate_promoted_ground_truth_required`。默认 `yolo`，没有执行模型导出、默认切换、Canary 或删除回滚权重。
- 工程 smoke 和契约测试此前 34/34 通过；P4 机器报告、Markdown 和四帧四模型定性证据均已生成。

P4/P5 产物：`reports/pose-backend-ab.json`、`docs/POSE_BACKEND_AB_REPORT.md`、`reports/pose-ab-evidence.jpg` 和 `runs/pose-ab/*`。下一步回到最小评分闭环：F0～F4 注册表、稳定主球员、事件、版本化特征和真值评测接口。

### 11.5 P6 实测结论

- 新增六指标机器可读 F0～F4 注册表和 Schema；当前六项均为 F0，验收阈值全部 `null`，没有经验 A～E 阈值。
- 主球员选择综合全区间 Track 覆盖、Pose 有效率、运动量、框面积和空间连续性；框面积权重仅 10%，不再逐帧选择最大框。
- 输出稳定 `primary_player_id=1`，同时逐帧保留 `source_track_id`，事件和特征可追溯回原 Detection/Pose。
- YOLO 冻结三视频 Track/Pose 覆盖：short 97.15%/96.46%，difficult 99.66%/88.32%，long 99.97%/97.16%。来源 Track 分别为 6/11/14 个，来源切换候选 6/14/31；人工身份真值缺失，confirmed ID Switch 保持 `null/ground_truth_required`。
- 最长 Pose 缺失降为 41/147/61 帧（1,367/4,900/2,542 ms），避免“只取最长单 Track”造成数千帧空洞；但这是语义 stitching 覆盖改善，不是身份准确率。
- 输出 Track 覆盖、来源切换候选、关键点有效率、左右交换、关键点跳变、最长缺失和 quality flags；Pipeline 已集成 `primary-player.jsonl` 与 summary。
- 新增纯函数测试后，全量测试 38/38 通过。

P6 产物：`metric-feasibility.json`、`contracts/primary-player.schema.json`、`reports/primary-player-baseline.json`、`docs/PRIMARY_PLAYER_BASELINE.md` 和三视频时间线。下一步 P7 仅基于该语义主球员实现 FS01/FS02/FS09 可解释事件基线与版本化特征。

### 11.6 P7/P8/P9 实测结论

- 六指标在机器注册表中均为 F2；F3/F4 未晋级，验收阈值保持 `null`。
- 冻结三视频输出 591 个 FS01/FS02/FS09 候选事件，以及 1,182 条指标结果；1,132 条 `calibration_required`、50 条 `unavailable`、0 条非空 grade。
- `events.jsonl` 携带边界不确定度、阶段、规则版本、事件内 Track 覆盖/来源切换候选/关键点有效率/左右交换/跳变/最长缺失；confirmed ID Switch 在无身份真值时为 `null`。
- 十个特征均使用 `timestamp_ms`，缺失写 `null`，输出单位、置信度、valid/reason、证据帧、raw/smoothed value 和函数版本。
- 人工事件/关键点导入与 Event F1、Segment IoU、Boundary MAE、特征 MAE/P95/Bias/分视角有效率、四类误差预算均有可执行接口；无真值报告为 `ground_truth_required`。
- 阈值规则和序数回归仅消费显式的 `coach_ground_truth_calibration` 资产；仓库没有阈值或模型参数。多教练 A～E 用 quadratic weighted kappa，排序用 Kendall tau。
- 真实 YOLO GPU 120 帧 Pipeline 烟测完成，FS01/FS02/FS09 各输出 1 个候选事件，六指标 4 条 `calibration_required`、2 条 `unavailable`；视频、frames、Pose SHA、Track、事件、特征和模型版本均可追溯。
- 当前全量回归测试和最终验收记录见 `docs/MINIMUM_SCORING_LOOP_ACCEPTANCE.md`。

## 12. 当前硬阻断与可继续工作

- 人工肩/髋/膝/踝真值、人工事件边界和教练标签目前不存在；相应精度、F1、F3/F4 和 A～E 结论必须保持 `ground_truth_required/calibration_required`。
- 这不阻止完成 Backend、契约、模型运行、性能/时序对照、标注数据包、特征函数和评测接口。
- 候选安装/导出失败不构成整体停止条件；优先尝试隔离环境和 ONNX Runtime，保持 yolo 环境不变。
