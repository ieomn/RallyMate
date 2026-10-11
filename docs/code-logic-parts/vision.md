# 视觉推理、跟踪、事件与特征代码逻辑详解

本文根据当前工作区源码静态梳理，覆盖 `rallymate_vision`、`rallymate_tracking`、`rallymate_events`、`rallymate_features`。没有启动模型推理，没有将测试文件的存在表述为测试已通过。文中的路径行号是本次阅读时的源码位置；`文件:行号` 指具体入口或实现起点。

## 1. 先确定这套代码实际完成什么

这一层输入一个本地视频和一份 `PipelineRequest`，逐帧生成检测框、人体关键点、摄像机运动诊断，随后离线选择主运动员，再按人体二维运动生成 FS01、FS02、FS09 候选事件、测量特征和评分所需的证据。它是“通用目标检测 + 可替换的自顶向下人体姿态后端 + 可追溯规则测量”的结构，没有在这里训练一个端到端网球技术评分网络。主流程见 `src/rallymate_vision/pipeline.py:397`。

必须分清以下概念：

| 概念 | 当前实现含义 | 源码入口 |
|---|---|---|
| player/ball/racket detection | 当前帧通用模型检测框 | `inference.py:97` |
| track_id | 简单跨帧关联器分配的对象编号 | `tracking.py:48` |
| primary_player_id=1 | 离线挑选出的分析对象逻辑编号，并非原始检测 ID | `primary_player.py:378` |
| body / body/s | 以人体画面尺度归一化的二维量，并非米或米/秒 | `coordinates.py:268` |
| FS01/FS02/FS09 event | 身体运动段及准备/启动/减速阶段候选 | `rules.py:848` |
| feature.valid | 当前工程测量门槛满足，不等于测量已经过独立真值验证 | `event_features.py:1037` |
| trajectory preview | 球框中心轨迹、有限插值及匀速外推展示 | `trajectory.py:929` |
| final scoring ready | 评分闭环报告明确返回 `scored` | `pipeline.py:1126` |

当前在线部署默认姿态预设是 `rtmpose-m-halpe26-online`，其注册状态明确为 `production_F2_measurement_default_not_F4_scoring`；这是二维测量默认，不表示正式教练评分已经获准。直接构造 `ModelConfig` 或省略请求中的后端字段时，底层默认仍为 YOLO + PyTorch + realtime；部署默认与底层兼容默认属于两个不同层级。见 `models/rtmpose/deployment-presets.json:4`、`:19`、`:29`，`contracts.py:21`。

## 2. 模块边界与总调用链

```text
python -m rallymate_vision / CLI 调用方 / worker 调用方
  └─ load_request(JSON) -> PipelineRequest
      └─ run_pipeline(request, 可选已加载 perception, progress_callback, 操作方信任配置)
          ├─ 校验视频与评分注册表/标定授权
          ├─ probe_video -> resolve_device -> Yolo26Perception
          │   ├─ Ultralytics detection model
          │   └─ PoseEstimator -> YoloPoseBackend / RtmposePoseBackend
          ├─ 从原视频开头解码，按原始时间截取并按 stride 采样
          │   ├─ SourceVideoClock.resolve
          │   ├─ analyze_frame
          │   ├─ detect -> HD 小球补充检测 -> 去重
          │   ├─ SimpleMultiClassTracker.update
          │   ├─ CameraMotionGuard.update
          │   ├─ Player ROI -> pose backend -> 原图关键点
          │   ├─ disabled court observation
          │   └─ validate_frame_observation -> frames.jsonl
          ├─ 导出保留源帧间隔的视频回放
          ├─ build_primary_player_artifacts -> 主球员时间线
          ├─ recognize_strokes_from_artifacts -> 挥拍/发球候选（独立支路）
          ├─ run_minimum_scoring_loop
          │   ├─ pose_sequence_from_records
          │   ├─ detect_pose_events
          │   ├─ compute_event_features / source-aligned measurements
          │   └─ 指标可行性、质量门控、标定与评分输出
          ├─ 计算准备度/测量组合/动作周期报告
          └─ summary -> validate_run_artifacts -> readiness -> HTML report
```

CLI 的职责很薄：解析 `--request`、`--max-frames`、`--device`、`--no-video` 与操作方信任资产路径，调用 `run_pipeline`，失败时输出 JSON 并以退出码 2 结束；成功时打印 job、帧数、耗时和下一阶段准备状态。见 `cli.py:13`、`:74`。`__main__.py:1` 只是模块启动器，`__init__.py:1` 是包说明。

## 3. 请求契约、默认值、预检顺序

### 3.1 请求结构

`PipelineRequest` 包含 `job_id`、`video_path`、`output_dir`、models、processing、court、scoring、upstream_metadata、request_path。JSON 中视频位于 `source.video_path`，输出目录位于 `output.directory`。相对路径以请求 JSON 的父目录解析，而不是以终端当前目录解析。见 `contracts.py:67`、`:79`、`:139`、`:178`。

| 配置 | 默认 | 作用 |
|---|---:|---|
| detect_imgsz | 960 | 整帧检测输入尺寸 |
| pose_imgsz | 640 | YOLO Pose 输入尺寸；RTMPose 实际读取其配置中的 codec 尺寸 |
| detect_confidence | 0.15 | 第一遍检测阈值 |
| pose_confidence | 0.25 | YOLO ROI Pose 推理阈值；RTMPose factory 没有接收该值 |
| device | auto | CUDA 可用时选 0，否则 CPU |
| pose_backend/runtime/profile | yolo/pytorch/realtime | 低层兼容默认 |
| start_ms/end_ms | 0/None | 原始视频时间的处理区间；end 为排他边界 |
| frame_stride | 1 | 从第一张满足起始时间的源帧开始，每 N 源帧做一次模型推理 |
| max_frames | None | 限制实际处理帧数，不限制从开头解码的帧数 |
| max_players | 2 | 按人物检测框面积选最大的 N 人做 Pose |
| court_every_n_frames | 25 | 契约保留字段，当前主流程没有使用 |
| write_annotated_video | True | 开启视频回放输出 |
| court.mode | disabled | 契约默认关闭，且主流程当前无条件关闭 |

默认值见 `contracts.py:21`、`:38`、`:49`；设备解析见 `utils.py:78`。

`load_request` 校验请求 schema、job_id、路径字符串、处理区间、置信度范围、后端/运行时/拓扑枚举、四点手工球场多边形和重复标定路径。scoring 只接受 feasibility_registry、reference_context、calibration_assets，拒绝其他字段。若声明 pose_preset，还要求模型路径、后端、runtime、profile、config、native format 与注册预设逐项吻合。见 `contracts.py:100`、`:148`、`:163`、`:183`、`:238`、`:262`、`:311`。

这里需要区分“契约允许枚举”和“后端确已实现”：请求校验允许 `onnxruntime`、`tensorrt`，但 YOLO 和 RTMPose 后端加载时都仅支持 PyTorch，前两者会被后端明确拒绝。见 `contracts.py:244`、`pose/yolo_backend.py:40`、`pose/rtmpose_backend.py:67`。

### 3.2 模型运行前就校验评分授权

`run_pipeline` 先确认视频存在并计算视频 SHA-256，然后经 lifecycle manifest 解析唯一授权的运行时 feasibility registry。普通任务 JSON 只能声明与授权路径完全相同的 registry，不能换成另一份实验注册表。该预检发生在建输出目录、加载 GPU 模型之前。见 `pipeline.py:416`、`:420`、`:230`。

如请求携带标定资产，`load_pipeline_calibrations` 只接受 threshold_rule 或 ordinal_regression backend，必须 `artifact_scope=production`，必须通过由操作方指定的 trusted promotion ledger 授权，并绑定当前 runtime registry；同指标重复标定直接报错。资产自称 passed 不是信任根。见 `pipeline.py:265`、`:291`、`:298`、`:323`。

生产标定还要求 runtime profile binding registry 与本视频 view evidence。view evidence 的视频 hash 必须与输入一致；其 video_id 作为稳定内容身份，job_id 保留为运行身份。若 reference_context 也声明了视频身份，两者必须吻合。见 `pipeline.py:449`、`:471`、`:490`、`:508`。

推理结束后、生成评分前、报告完成前会重复确认 lifecycle manifest 和 registry 内容未变；标定情况下还核验 runtime binding、view evidence 与输入视频 hash。这样一次运行不能前半程使用某个授权版本、后半程偷偷换版本。见 `pipeline.py:252`、`:837`、`:855`、`:901`、`:1221`。

## 4. 逐帧推理：每一步如何串起来

### 4.1 视频探测与解码

`probe_video` 用 OpenCV 读取宽、高、平均 FPS、帧总数；任一关键值不正则报错。`duration_ms=frame_count/fps*1000` 是元数据估计，不是逐帧实测 PTS 末值。见 `quality.py:21`。

主流程用平均 FPS 估计计划帧数，供进度展示；真正决定是否处理某帧的是 `SourceVideoClock` 解析的 timestamp。为兼容可变帧率视频，代码从源视频开头顺序解码，而不是直接用 `start_ms*fps` seek。每张源帧都推进时钟，先判断 `timestamp>=end_ms` 则停止，再跳过 `timestamp<start_ms`，最后依据源帧编号执行 stride。见 `pipeline.py:575`、`:600`、`:633`。

因此有三个独立索引：`frame.index` 是原视频帧号；`frame.processed_index` 是模型处理序号，连续从 0 开始；`timestamp_ms` 是原视频播放时轴毫秒。比如 stride=3 后源帧可能是 100、103、106，而 processed_index 仍为 0、1、2。不能将 processed_index 当原视频帧号，也不能把模型耗时当运动时间。见 `pipeline.py:703`。

### 4.2 时钟如何处理不可靠的解码时间

`SourceVideoClock.resolve` 要求源帧号严格递增，首帧合法 decoder_ms 建立时间原点；合法且严格递增的归零 decoder 时间记录为 `decoder_pts`。非法/重复/倒退时间回退为帧号除平均 FPS，并保证时间继续递增，明确标注 `fps_fallback`。summary 记录两类帧数，只有零 fallback 且至少两张 decoder 帧时 timing_verified 才为 True。见 `timebase.py:18`、`:40`。

fallback 只保证索引可继续处理，不给它真实运动时间的证据地位。主流程将该帧 camera_motion 标为 unavailable、清空补偿矩阵并重置参考坐标；姿态播放也不在 fallback 帧绘制骨架。见 `pipeline.py:674`、`pose_playback.py:203`。

### 4.3 画质指标

每个实际处理帧先转灰度，计算平均亮度、标准差对比度和 Laplacian 方差模糊分数：亮度 <45 太暗，>220 过曝；对比度 <25，模糊分 <45 给相应 warning。整段报告给中位数，当某 warning 覆盖至少 25% 的采样帧时提升为全局警告。低于 720p、低于 24 FPS、低于推荐 50 FPS 也给元数据提示。见 `quality.py:43`、`:65`、`:77`。

画质 warning 在这一步不直接取消检测；它是后续解释和质量门控的证据。质量模块也没有训练出图像美观或网球评分模型。

### 4.4 检测模型与 HD 小球补检

`Yolo26Perception` 名称含 YOLO26，但代码真正加载的是请求指定的任意本地 Ultralytics YOLO 检测权重；接受 person/player、sports ball/ball、tennis racket/racket 名称映射，归一为 player、ball、racket。初始化模型后扫描模型 names，只请求相关类别。见 `inference.py:29`、`:64`、`:91`。

第一遍整帧 predict 使用 detect_imgsz、detect_confidence。若源视频长边 >=1600，基础检测尺寸小于默认 refinement 1536，并且模型有 ball 类，再对球类单独跑 1536 尺度，置信度至少 0.25。补检只接受短边不超过源图短边 3%、长宽比 <=3.5 的小框，以抑制大圆形招牌。见 `inference.py:97`、`:111`、`:172`。

合并两尺度时，同类框面积比不超过 4，IoU>=0.3 或 intersection/min-area>=0.65 才视为同球观测，保留置信度更高者；无匹配补检框追加。随后全类别去重，player/racket/ball 的 IoU 阈值分别 0.82/0.85/0.92，人物嵌套重叠 >=0.90 也去重。见 `inference.py:180`、`:218`。

每个检测记录同时输出 bbox_px、bbox_normalized、center_px、center_normalized、confidence、class_id、source_class_name、downstream_systems；track_id 初始 None，交给下一步填写。像素坐标裁剪到画面范围，归一化分别除宽和高。见 `inference.py:127`。

### 4.5 在线对象关联器

`SimpleMultiClassTracker` 没有外观 ReID、卡尔曼滤波、匈牙利全局匹配或球体物理模型。它将当前检测按置信度从高到低排序，只匹配同类别且未被其他当前检测占用的旧轨迹。候选代价：

```text
cost = 0.65 * (1 - IoU) + 0.35 * normalized_center_distance
```

当 IoU<0.01 且归一中心距离超过阈值时拒绝关联；ball 距离阈值 0.28，其他类 0.16。选最小代价，找不到则分配单调增加的新 ID。返回的只是真实当前检测，不给漏检帧补出预测框。见 `tracking.py:48`、`:70`、`:93`。

人物最多容忍 30 个未匹配处理帧、球拍 12 帧、旧式无 timestamp 的球 8 帧；主流程有 timestamp，球使用“距上次检测 <=300ms”的时间寿命，不因 FPS 或 stride 改变。时间倒退会清空活动轨迹，避免新片段继承未来身份。见 `tracking.py:24`、`:41`、`:56`。

人物/球拍漏检期限仍按处理帧数计算，所以 frame_stride 改变会改变其实际时间跨度；球已经采用源时间门控。这里的 track 稳定性不等于经过人工身份真值验证。

### 4.6 摄像机运动补偿

摄像机估计在人物检测后做，先将画面缩至长边最多 640，遮住人物框并加边距，避免将人物运动当背景运动。上一帧提最多 160 个角点，用前向/反向 Lucas–Kanade 光流检查，回投误差 <1.5 像素且仍在未遮挡背景内的点才能参与 RANSAC。见 `camera_motion.py:30`、`:60`。

至少 16 个对应点、16 个内点、内点比例 >=0.7，且在 3×3 网格中至少覆盖 4 个格子，才接受 `estimateAffinePartial2D` 的相似变换。dt 必须在 (0,0.5s]，单步 scale 在 [0.8,1.25]；累计线性矩阵行列式在 [0.25,4]。见 `camera_motion.py:58`、`:80`、`:88`、`:98`。

参考矩阵递推：`current_to_reference = previous_to_reference @ inverse(previous_to_current)`。光流是在缩图上算的，平移先除缩放 factor 恢复到源像素。背景平移/对角线/秒<=0.012、旋转<=1.5°/秒、尺度变化<=0.015/秒被标为 stationary，否则 moving；两者都可能有有效补偿。首帧为 reference。不能验证时 unavailable，累计矩阵清空，reference_epoch 增加。见 `camera_motion.py:43`、`:92`、`:102`。

这只是背景图像平面稳定化，不能纠正透视深度变化、三维身体旋转，也不是球场米制标定。关键点可转到同一参考图像坐标，但不产生 3D。

## 5. 姿态后端、ROI 与关键点契约

### 5.1 公共协议

`PoseBackend` 规定 load、infer(rois,timestamps_ms)、metadata。输出每个 ROI 恰好一个槽位：`PoseBackendOutput` 或 None；xy 为 K×2 ROI 局部像素坐标，scores 为 K。metadata 包括后端/runtime/profile、模型与 config 路径及 hash、原生拓扑、输入尺寸、加载耗时及 score 语义。见 `pose/base.py:10`、`:34`、`:51`。

协议预留 timestamp，但目前 YOLO 和 RTMPose infer 都 `del timestamps_ms`，是逐帧姿态网络。主 facade 的 `estimate_poses` 也没有传入主流程 timestamp。因此不能根据接口参数宣称这里采用时序 Pose 网络。见 `inference.py:251`、`pose/yolo_backend.py:60`、`pose/rtmpose_backend.py:126`。

### 5.2 统一 ROI 适配器

`PoseEstimator.estimate` 按人物框面积降序选择 max_players，然后每侧按框宽/高扩 15%，裁到图像边界；宽或高小于 32 像素的 ROI 不送模型。见 `pose/adapters.py:16`、`:39`、`:51`。

模型输出 xy 加 ROI 的左上角偏移回原图；按 schema 顺序附 name/index/downstream_joint_id，同时写像素和 0..1 坐标。非有限坐标或置信度直接报错；出画面坐标会保留 raw_x/raw_y，并设置 in_frame=False，序列化主坐标裁剪到边界；超出 0..1 的 confidence 保留 raw_confidence、confidence_in_range=False。这样绘图需要的有界坐标和测量需要的“真实越界”语义都保留下来。见 `pose/adapters.py:75`、`:87`、`:108`。

Pose 记录的 `person_track_id` 指向同帧 player 检测 track_id，形成关联边；不会用 pose 的顺序推断人是谁。见 `pose/adapters.py:124`。

### 5.3 YOLO backend

加载 Ultralytics pose 权重，只支持 PyTorch；所有 ROI 作为 list 批量 predict；每个 ROI 如果模型预测多人，取检测框 confidence 最大的那个人。未检出则 None；如果 backend 没给 keypoint confidence，就用全 1 数组。metadata 固定 COCO17，pose_score 是 ROI 人体框置信度。见 `pose/yolo_backend.py:37`、`:54`、`:81`、`:97`。

这意味着 ROI 内混入邻近人时，后端本身不会做跨人姿态身份推理；它取置信度最大的 ROI 人。相关误差需要由 ROI 设计、跟踪/质量诊断和后续真值检查发现。

### 5.4 RTMPose backend

必须给 checkpoint + MMPose config；支持注册的 Halpe26/WholeBody133 等拓扑。device=数字转成 `cuda:N`，支持 cpu/cuda:N。加载时补一个 mmcv-lite 兼容 stub 避免注册不使用的 EDPose，移除 backbone 的 scope 并设 mmpose scope。analysis profile 开 flip_test，realtime 关闭；实际 H×W 来自 codec.input_size，并不采用普通 pose_imgsz 的 640。见 `pose/registry.py:34`、`pose/rtmpose_backend.py:44`、`:55`、`:90`。

加载官方 checkpoint 时临时包装 `torch.load`，默认 weights_only=False，然后 finally 恢复原函数；这是对受信任本地 checkpoint 的兼容做法，不应把任意用户文件直接当 checkpoint。infer 对每个 ROI 分别调用 MMPose `inference_topdown(model,roi,bboxes=None)`，取第一份实例，检查 K×2/K 形状。pose_score 是关键点分数均值，不是 YOLO 的框分数，跨后端不可把该 score 当同一个校准概率。见 `pose/rtmpose_backend.py:99`、`:120`、`:152`。

### 5.5 拓扑注册表与模型预设

`pose/metadata.py` 缓存读取 `pose/data/keypoint_schemas.json` 中 COCO17/Halpe26，运行时补 WholeBody133：身体17 + 足部6 + 面部68 + 左手21 + 右手21。只有前17继承既有下游关节 ID，额外点明确无映射，不能因为模型有133点就宣称所有指/面/足部评分指标已接好。schema 校验 count 与连续 index。见 `pose/metadata.py:13`、`:79`、`:91`、`pose/data/keypoint_schemas.json:1`。

预设解算从 `models/rtmpose/deployment-presets.json` 读取，优先工作区中的 MMPose config，不存在时查已安装 mmpose 包路径；默认要求权重/config 存在。见 `pose/presets.py:40`、`:59`、`:76`。

| 预设 | 拓扑/尺寸 | 定位 |
|---|---|---|
| yolo-baseline | COCO17 / 640×640 | 明确回滚与粗姿态基线 |
| rtmpose-m-halpe26-online | Halpe26 / 256×192 | 当前在线 F2 测量默认 |
| rtmpose-m-halpe26-analysis | Halpe26 / 384×288 | 离线分析候选 |
| rtmpose-l-halpe26-analysis-shadow | Halpe26 / 384×288 | 离线 shadow，未 accuracy validated、未 F4 |
| rtmpose-m-wholebody133-analysis | WholeBody133 / 256×192 | 离线全身细节候选 |

注册定位见 `deployment-presets.json:7`、`:19`、`:32`、`:45`、`:58`。analysis 是测试配置与角色标识，不是自动证明精度高于在线模型。

## 6. 原始帧产物与离线主运动员选择

### 6.1 frames.jsonl 是后续所有分析的共同输入

每帧写 schema_version=1.1.0、job_id、frame、quality、camera_motion、detections、poses、court、handoff。handoff 明确人体关联、原图坐标、源视频毫秒与 J/BALL/RK/S05 分类映射。每帧在写文件前调用 `validate_frame_observation`。进度更新之前 flush，保证 API 的实时轨迹读取看到完整行。见 `pipeline.py:703`、`:732`、`:751`。

写入成功不等于任务整体成功：后面的主球员、事件、特征、报告仍可能失败，所以不能仅凭 frames.jsonl 非空将任务标 completed。

### 6.2 在线 track_id 与离线 primary_player_id

`build_primary_player_artifacts` 读完整 frames.jsonl 到内存；首先每帧将检测与 Pose 通过 track_id 连接成候选，计算面积、检测置信度、8个关键躯干/腿关节的有效比例。这里选择器使用 0.20 的较宽松关键点阈值，而后续测量 point_series 使用 0.25，两者并不矛盾：前者用于身份选择，后者用于量测。见 `primary_player.py:16`、`:103`、`:134`、`:706`。

对每个原始 track 计算全片先验：

```text
prior = 0.50 * 出现覆盖率
      + 0.25 * 平均有效关节比例
      + 0.10 * 归一化中位框面积
      + 0.15 * 归一化平均中心移动量
emission = 2.0 * prior + 0.70 * 当前姿态有效率 + 0.20 * 当前检测置信度
transition(同 track) = +1.5
transition(换 track) = -0.40 - 3.0 * 归一化中心距离 + 0.50 * IoU
```

先验定义见 `primary_player.py:184`；转移见 `:229`；动态规划见 `:249`。没有候选人物的帧将序列切成独立段。每段做 Viterbi 累积并回溯最优整段路径，不是每帧简单挑面积最大的框。

额外计算反向 suffix 分数，使每帧 best/second_best/margin 描述完整段路径上的竞争，而不是只看在线前缀。候选按 track_id 和框坐标排序，分数精确并列时确定性取第一个，同时 `identity_ambiguous=True`；不把排序选出的唯一返回对象假装成真实身份唯一。见 `primary_player.py:164`、`:285`、`:323`。

时间线逐帧记录逻辑 primary_player_id=1、真实 source_track_id、selected/missing、pose_present、有效率、prior、候选数、整段分数与歧义、算法版本 primary-player-v0.3.0。见 `primary_player.py:378`。这个逻辑 ID 可以跨原始轨迹碎片，但没有外观身份重识别真值作保证。

### 6.3 身份与关键点诊断

`diagnose_primary_timeline` 可对全片或事件窗口诊断，重新从当前 frames 的 pose 计算有效率，避免复用另一个后端的旧值。统计选择覆盖、姿态覆盖、源轨迹切换候选、歧义、最长缺 Pose、身体尺度。见 `primary_player.py:507`、`:617`。

左右互换候选：比较相邻帧同名点距离之和与左右对调后的距离之和，若交换匹配 <直接匹配×0.8，且改进至少0.05 body，标记可疑帧/关节对。跳点候选：关节速度超过 median+6×1.4826×MAD，且单步位移>=0.20 body。见 `primary_player.py:573`、`:586`、`:598`。

人物覆盖<0.80、Pose覆盖<0.75 给质量 flag。`confirmed_id_switch_count` 始终 None，状态 ground_truth_required；跳点、换 ID、左右交换都只是候选诊断，不冒充准确率。见 `primary_player.py:622`、`:648`。主时间线还必须与 registry 要求的选择算法版本一致，混版本或部分缺失版本会被拒绝。见 `primary_player.py:41`、`:86`。

## 7. 三套坐标必须分开理解

1. **模型 ROI 局部像素**：后端返回的 `[x_roi,y_roi]`，只在 PoseBackend 边界存在。
2. **视频存储/播放坐标**：原图 `[x_px,y_px]` 与 `[x/W,y/H]`，适合放回视频，不适合直接计算欧氏角度和距离。
3. **测量坐标**：先用当前像素到参考画面的有效相似变换，再将 x、y 都除以 `max(W,H)`；两轴单位相同，不受横纵比例不同造成的角度扭曲。之后按事件中位 body_scale 得到 body、body/s 等相对单位。

测量转换见 `coordinates.py:56`、`:85`。像素字段只要有一个出现，就要求成对合法，不能拿另一份 normalized 值悄悄补救损坏像素。越界点 in_frame=False 视为缺失。没有合法画面尺寸也视为缺失，不猜历史视频比例。变换后的参考点可能落到当前画面外，不能再裁剪，否则会扭曲运动轨迹。见 `coordinates.py:58`、`:63`、`:80`。

只有 declared compensation_valid=True、2×3 有限矩阵、正行列式 [0.25,4]，并满足线性矩阵 `AᵀA≈det(A)I` 的相似变换才被接受；这排除翻转、剪切和不等轴缩放。moving+有效补偿在测量序列中改名 compensated；无效矩阵或缺参考 epoch 对应帧整体 NaN。见 `coordinates.py:16`、`:122`。

`PoseSequence` 组织 T 长度严格递增的 timestamps/source_frames，每个关节 T×2 坐标、T 置信度，附每帧 W/H、camera status、transform 和 epoch。缺失为 NaN，序列化证据时转 null。见 `schemas.py:10`、`event_features.py:368`。

人体中心并非物理重心：shoulder_center=左右肩中点；hip_center=左右髋中点；body_center=肩中心与髋中心的中点。body_scale 取同一帧肩宽、髋宽、肩髋中点距离中有效正值的中位数，特征再取事件尺度中位数。见 `coordinates.py:238`、`:259`、`:268`、`event_features.py:355`。

## 8. 事件识别：从身体运动到 FS01/FS02/FS09

### 8.1 事件候选前置条件与相机窗口

当前 detector 版本 pose-motion-bout-v0.5.0，phase版本 pose-event-phase-proxies-v0.3.0。至少8帧才尝试检测；只有 reference/stationary/compensated 相机帧可参与。先按 unavailable/moving 未补偿帧以及 epoch 变化切分连续窗口，再在各窗口里平滑和求导。否则单纯 NaN masking 会被插值桥接，让换镜头变成虚假的人体高速移动。见 `rules.py:16`、`:848`、`:866`。

每窗口递归检测，event ID 带窗口来源保证唯一；返回时把窗口局部 provenance 索引补回全片索引，并保留 camera_window。没有 camera 信息的旧数据可以走 legacy 分支，但追加 camera_motion_unverified。见 `rules.py:883`、`:1303`。

### 8.2 身体运动参考不是补人体关键点

优先用肩髋组成的 body_center；肩缺失但双髋可见时，用“髋中心 + 全窗口双参考重合帧的 body_center-hip_center 中位偏移”代替运动参考。若整个窗口从未有 body_center，则可用直接髋中心。记录哪几帧来自身体中心、髋 fallback、缺失，以及偏移估计依据。这里只为分段保留平移线索，不修复关键点，不把髋中点解释为物理重心。见 `rules.py:241`、`:325`。

### 8.3 自适应运动段

body_scale 与 center 在窗口内做 max_gap=200ms、radius=120ms 平滑，计算 center 速度除逐帧平滑 body_scale，再平滑速度。有效 mask 仍要求原始 center/scale 有效。见 `rules.py:921`。

每帧在 ±1500ms 局部时间窗里估计活动阈值，时间权重每点最多160ms，防止高FPS部分或长缺口主导分位数：

```text
active_threshold(t) = weighted_Q20 + 0.45 * max(weighted_Q80 - weighted_Q20, 1e-6)
active = valid AND speed >= active_threshold
```

至少4个有效局部速度样本才有阈值。连接 <=240ms 的短活动缺口，候选段跨度必须 >=240ms。见 `rules.py:803`、`:940`。

随后用段首尾各20%位置中位数计算持续位移，并检查持续位移>=0.10 body、持续位移/总路径>=0.20、相邻非零运动方向余弦均值>=0.80。任一不满足即删除抖动候选。静止视频无符合条件的段会返回空列表，不会强行围绕最大数值制造事件。见 `rules.py:106`、`:949`。

### 8.4 一个运动段派生三个区间

对每个 accepted bout：

- FS01：onset 前最多1000ms到 onset，且不早于前一运动段结束。
- FS02：onset 到 active_end。
- FS09：本段速度 peak 到 active_end 后最多1200ms，受下一段 onset 和视频结尾限制。

边界不确定度 `max(2*median_dt,40ms)`。每个区间独立计算运动信号有效覆盖率，阈值0.75；不能拿 FS02 覆盖率替代 FS01/FS09 的覆盖率。置信度取覆盖率×峰值突出度函数，并一律封顶0.75，因为缺事件真值。见 `rules.py:981`、`:988`、`:1307`、`:1327`。

### 8.5 相位到底是什么意思

| 事件/相位 | 当前计算 | 不能据此声称 |
|---|---|---|
| FS01 preload_ms | 准备窗中有效髋中心 y 最大处 | 已准确定位技术定义的预加载全过程 |
| takeoff_proxy_ms | 踝点画面向上速度活动起点 | 双脚真实离地时刻 |
| landing_proxy_ms | 踝点向下活动结束后的减速候选 | 真实触地时刻 |
| redistribution_ms | 髋在双踝支撑轴投影变化的活动起点 | 实际重心/体重转移 |
| initiation_ms | 身体平移活动段 onset | 神经反应时间 |
| FS02 direction_conversion_ms | 最明显方向变化；不成立时首个可靠行进方向 | 朝正确战术目标移动 |
| support_extension_proxy_ms | 髋到踝长度增长率峰候选 | 肌肉发力或地面反作用力 |
| lead_foot_motion_onset_proxy_ms | 相对髋速度更突出一侧踝的活动起点 | 已确认领先脚身份 |
| first_step_slowdown_proxy_ms | 领先踝速度峰后正减速度活动起点 | 第一步落脚触地 |
| FS09 peak_speed_ms | 事件运动参考速度峰 | 校准的真实最高米制速度 |
| deceleration_peak_ms | FS09 段负速度导数最大处 | 制动力/冲量 |
| restabilization_onset_ms | 峰后首个速度低于自适应阈值的有效样本 | 稳定控制已确认 |
| stable_control_onset_ms | 当前是 restabilization 同义候选 | 已有人工标注稳定控制阶段 |

定义集中在 `rules.py:35`；踝/髋相位信号在 `:655`；phase 构造在 `:1020`、`:1051`、`:1095`、`:1195`、`:1268`。

相位活动阈值取局部 Q20+0.35×(peak-Q20)，至少3点且有非数值噪声的突出度；找包含峰值的活动 run，以 run_start、peak、run_end 后一个样本为 onset/peak/slowdown。见 `rules.py:384`、`:419`。

右边界截断时可能只返回可见峰，明确加 `phase_proxy_right_censored_peak:<name>`；只有两点的尾部可以返回 `phase_proxy_low_sample_peak:<name>`。这些是低支持展示/测量锚点，带阻止正式评分的 flag，不伪造 slowdown/contact。双踝相对速度峰差不到5%则标领先脚候选歧义。见 `rules.py:456`、`:497`、`:528`、`:1108`。

事件记录由 `_make_event` 输出 start/end、key_phases_ms、confidence、boundary_uncertainty、quality_flags、detector/phase版本、阈值来源、事件局部覆盖率、运动参考来源、source_id/video_id，并立即走 schema 校验。见 `rules.py:745`。当前 schema 事件类型集合只有 FS01、FS02、FS09，见 `events/schemas.py:8`。

## 9. 特征基础层：缺失、平滑、求导、统计

### 9.1 缺失点与平滑

`valid_point_mask` 要求 T×2 有限坐标、有限置信度且 confidence>=0.25；`point_series` 对无效点设 NaN，缺整类关节则返回全NaN和全False mask。见 `validity.py:9`、`coordinates.py:221`。

`interpolate_short_gaps` 逐列只对两个有效端点之间跨度<=160ms的内部缺口按真实时间线性插值，不外推头尾，也不填长缺口。`time_weighted_smooth` 用 ±100ms 三角时间权重，至少2个有效样本；实现采用 searchsorted 和有界批次，避免构造 T×T 距离矩阵。`smooth_series` 先插值再平滑。见 `smoothing.py:6`、`:36`、`:80`。

缺失可以被短缺口平滑支持，但 raw_value 仍保留原始缺失，不能认为输出的平滑值是模型真实观察点。事件检测特定阶段将 gap/radius 改成200/120或200/80，这是其单独版本化规则，不能拿默认160/100替代解释。

### 9.2 求导有一处明确的生产/实验分界

当前生产 `irregular_derivative` 对每一列找到所有有限观测，取前后有限邻居割线 `(value[right]-value[left])/(t[right]-t[left])`，端点单边求导。它确实使用真实毫秒差，而非固定FPS；但可能跨过中间缺失点或长时间间隔。`speed` 是二维导数范数；`scalar_acceleration` 再对速度求导。见 `kinematics.py:55`、`:86`。

同文件的 `contiguous_irregular_derivative` 是明确 opt-in candidate：只在相邻索引、时间gap<=160ms的连续有限区间内求导，孤立点无导数。注释说明尚未替换生产 primitive，需要独立的 feature/event registry 合同后再提升。不能看到更严格的函数存在，就把现有生产行为描述成不会跨缺口。见 `kinematics.py:11`。

几何层用三点夹角、`atan2(-y,x)` 图像向上为正方向、轴角wrap到[-90,90)、倍角unwrap消除180°方向歧义。见 `geometry.py:6`、`:24`、`:31`、`:36`。

### 9.3 持续时间不是简单数帧

`longest_contiguous_duration` 只连续相邻样本、时间gap<=160ms且 mask=True 的段；按实际跨度最大选段，再加一个从真实连续间隔计算的中位 dt 作为端点支持。缺视频长空洞不参与中位 dt，不延长持续时间。见 `temporal.py:12`。`validity.longest_false_run` 则服务缺失诊断，返回最长缺失数量和近似时长，见 `validity.py:41`。

## 10. 通用特征调度与证据结构

`FeatureResult` 有 name、version、value、unit、confidence、valid、reason、source_frames、raw_value、smoothed_value、provenance。它不仅输出一个数，而是附出这个数的来源；to_dict 默认深拷贝，评分写入方可选 copy_evidence=False，共享不可变证据但复制 provenance，减少整片数据重复拷贝。见 `features/schemas.py:67`。

`compute_feature` 先确定事件能否完整落在同一可信摄像机参考窗口；跨拒绝帧或 epoch 的事件整体不可测，不能截短成“还能测的半个事件”。允许窗口扩展到同参考 epoch 的最大连续区域，再做平滑导数，使人工事件也遵守同一边界规则。见 `event_features.py:1137`、`:1215`、`:1232`。

`_compute_feature` 检查注册名称，取事件闭区间内时间样本，少于2帧或无 body_scale 则返回 invalid。依名称分发到稳定时长、FS01/FS02事件特征、FS09事件特征或通用序列。通用序列按 sequence 对象缓存一次，先以 scale=1生成 raw和smoothed，再按事件body_scale缩放。见 `event_features.py:589`、`:1037`。

通用特征 valid_fraction>=0.50 且聚合值可用时 valid=True；confidence=所需关节平均置信度×该特征有效覆盖率。聚合支持 median、peak、peak_positive、绝对值最大但保留符号、相对双踝中点最大偏离、circular_mean。代表帧含峰/中位/圆均值代表帧及首末有效源帧。见 `event_features.py:397`、`:420`、`:451`、`:1104`。

基本10特征见 `event_features.py:46`、`:78`：

| 名称 | 当前数值定义/单位 |
|---|---|
| hip_center_y_body | 踝支撑中点到髋中心的向上高度/body，事件中位数 |
| body_center_speed_body_s | 肩髋中点的二维速度/body，峰值 |
| left/right_knee_flexion_deg | 180°−髋膝踝内角，峰值 |
| torso_lean_deg | 躯干相对图像竖直轴倾角，绝对值峰保留符号 |
| stance_width_body | 双踝二维距离/body，事件中位数 |
| hip_center_relative_to_ankle_support | 髋中心投影到左踝→右踝轴，0=左、1=右，取最偏离0.5样本 |
| body_center_deceleration_body_s2 | 平滑归一化身体速度的负时间导数，正峰 |
| stability_duration_ms | 同时低身体速度、低肩髋分离角速度的最长连续区间 |
| shoulder_hip_angular_velocity | unwrap后的肩轴角−髋轴角的时间导数 |

稳定时长的包络由事件自身速度35百分位、绝对角速度50百分位形成，至少3个联合有效样本；是描述本事件相对低活动的 proxy，不是“达到教练定义稳定”的外部阈值。见 `event_features.py:621`。

fine-foot diagnostic 还提供 left/right_ankle_shank_foot_angle_deg，使用膝—踝—大小脚趾均值三点夹角，COCO17 缺脚趾时不可用；即使有足部点，也只称二维诊断而非校准踝背屈等级。见 `event_features.py:149`、`:548`。

## 11. FS01/FS02 专项特征的具体规则

该大模块把“脚部参考、动作侧选择、运动方向、事件局部聚合、证据包”分开实现；入口 `summarize_fs01_fs02_event_feature` 位于 `fs01_fs02_features.py:2216`，定义表位于 `:76`。每个分支共享纯函数，供生产测量和无额外平滑的误差归因实验复用，避免实验用另一套公式。

### 11.1 脚参考点与 fallback

优先取同侧 heel/big_toe/small_toe 三点质心，三点均有效才算 fine-foot。若拓扑原本没有这三点（COCO17），可显式用 ankle。若拓扑有细脚但某帧缺失，只有曾在同帧同时观察到细脚质心与 ankle，才能用全序列稳健中位 `fine_centroid-ankle` offset 对 ankle 对齐；没有重合观测时 fallback 标 unaligned/unusable，避免切换参考点导致假位移。记录每种来源 mask、比例、置信度折扣、实际所需关节。见 `fs01_fs02_features.py:270`。

### 11.2 FS01 双脚起伏与减速后测量

| 特征 | 如何算 | 函数起点 |
|---|---|---|
| bilateral_foot_rise_min_body | 每侧用事件前25%有效样本 y 中位值作局部基线，取向上最大偏移，再取左右较小值/body | `:979`、`:1112` |
| bilateral_foot_rise_synchrony_ms | 左右脚参考点向上速度峰时间的绝对差 | `:1175` |
| bilateral_foot_rise_proxy_duration_ms | 两脚同时超过各自局部上升峰50%的最长连续时间 | `:1236` |
| hip_center_vertical_velocity_body_s | 髋中心画面向上速度的正峰/body | `:1383` |
| bilateral_foot_vertical_slowdown_time_offset_ms | 左右脚向下活动后的竖直减速候选时间差 | `:1423`、`:1524` |
| post_slowdown_stance_width_body | 两侧都出现竖直减速候选之后的脚参考点间距中位数/body | `:1576` |
| hip_center_lateral_variability_body | 两侧减速较晚时点之后的髋x/body总体标准差 | `:1620` |

rise duration 的0与None严格区分：只有完整观察、确实没有双侧同时半峰活动段，才返回0；缺位置或时间gap过大保留不可测。见 `fs01_fs02_features.py:1251`。髋横向波动若段边界后只剩1个有效点，可取160ms内最近前一个事件内点形成2点边界截断诊断，明确标 censored；不能把这称完整落地后稳定区间。见 `:1688`。

### 11.3 FS02 发力侧、起动方向、第一步

`launch_direction_from_hip_motion` 用事件髋位移 + 髋中位速度×事件时长得到合成向量并单位化，方向0°向画面右、90°向上。向量退化返回 unavailable，不借助球场/目标落点推断正确方向。见 `fs01_fs02_features.py:404`。

drive_side_code 比较左右二维膝伸展速度正峰：-1左、0不确定、1右。`support_knee_extension_velocity_deg_s` 取选定侧或最强观测的值；如果两侧都没有正伸展，也保留非正值作为可测负证据，不自动当 missing。`support_drive_to_moving_foot_rise_proxy_ms` 用选中膝峰到对侧脚向上速度峰时间差；`hip_acceleration_along_launch_direction_body_s2` 把髋加速度投影到起动方向取正峰。见 `:2088`、`:2119`、`:2564`、`:2590`、`:2628`、`:2666`。

`launch_foot_event_kinematics_from_series` 在同一时间网格联合消费髋位置、双脚位置/速度，沿起动方向比较两脚正位移选移动脚；输出 launch_side_code、选中脚速度峰、两脚投影位移差、选中脚投影位移、超过正投影速度半峰的最长活动时长。相关数值集中来自同一 shared result，避免不同指标各选不同侧。见 `:512`、`:2720`。

`first_step_phase_kinematics_from_series` 固定使用事件已声明的 first_step_slowdown_proxy_ms 锚点；只对 legacy 未提供相位的调用者保留由速度计算 slowdown 的 fallback。人工相位允许在事件内最近源样本对齐，最大160ms；已声明但无法对齐则失败，不偷偷换成另一个相位。见 `:704`、`:2133`。

第一步输出包括 launch_foot_speed_drop（速度峰减峰后末端速度中位数）、first_step_displacement、post_step_hip_direction_consistency（髋速度方向与启动方向余弦均值）、slowdown_to_post_hip_direction_ms、post_step_stance_width_body。它们在 `:2939`、`:2959`、`:2982`、`:3006`、`:3032` 汇总。依然没有脚接触、真实着地距离、负重比例或力量测量。

## 12. FS09 专项特征：减速与重新稳定

`fs09_features.py` 将12个逐帧系列与13个事件摘要分开，列表见 `:52`、`:69`。逐帧系列包括髋vx/vy、speed、direction、髋相对双踝中点x/y、速度趋势/减速度、左右踝速、左右膝屈曲角速度；所有导数用 timestamp，位置/速度按body_scale归一化。见 `:119`—`:247` 的各入口。

`event_edge_change` 需要至少4个有限点，取前/后25%有效样本（每端至少2点，且不重叠）的中位数，按特征定义做 early−late 或 late−early：速度 drop 是前减后，膝屈变化/髋高变化为后减前。`event_variability` 为总体标准差 ddof=0。见 `fs09_features.py:286`、`:318`。

braking_side_code 首先选净速度下降更大的脚；若两脚净变化都非正，但某侧局部正减速峰可见，则用更强局部峰判候选侧，保留对应脚的零/负净drop。两个量完全并列返回0，缺必要观测返回None。这样“没有净减速”与“没观察到”不会混为一谈。见 `:336`、`:808`。

double_support_low_motion_proxy 取事件左右踝速度各自中位数作为包络，找两侧同时低于各自包络的最长连续时间；它不测真实双脚触地。两个时序量分别为“髋减速峰−所选踝减速峰”和“双踝低活动proxy起点−髋减速峰”。均保留原/平滑系列与证据索引。见 `:425`、`:519`、`:611`、`:897`。

FS09 数据系列同样按 sequence identity 缓存，`clear_fs09_feature_cache` 负责清理；中央 clear_feature_cache 联动清理通用、FS01/02、FS09缓存。见 `fs09_features.py:672`、`:724`、`event_features.py:605`。

## 13. 对齐原始技术条款的补充测量 source_alignment.py

该模块是现有特征的补充，不改写旧特征单位与含义。当前只处理 FS01-M02/M04/M05、FS09-M05，保留对应 Word XML 段落 locator。返回的 grade、technical_score_0_to_100 一律 None。见 `source_alignment.py:1`、`:22`、`:28`、`:484`。

实现的归属也要分清：真正数学与门控实现在 `src/rallymate_features/source_alignment.py`；`src/rallymate_scoring/source_aligned_features.py:2` 只重新导出 `SourceAlignedContext`、prepare、compute、clear 四个公共符号，没有第二套算法。`src/rallymate_scoring/source_assessment.py:37` 负责产物封装：对 FS01/FS09 事件逐个计算，finally 清缓存，绑定技术条款版本与 hash、video/frames/events/primary timeline hash，并以临时文件替换方式写入 `source-aligned-measurements.json`；文件明确声明 `source_aligned_measurement_not_technical_grade`，技术等级和技术分均为空。见 `source_assessment.py:44`、`:50`、`:61`。当前主闭环在旧 features/indicator-features/scores 写完后再生成这份附加产物，不据此回写原 scores，见 `src/rallymate_scoring/loop.py:602`、`:605`。

门控比通用特征更严格：所需关节必须在至少70%样本中同时可见；点阈值仍为0.25，明确这只是工程置信度门槛。时间窗必须完整被采集，源帧号逐个相邻、时间间隔<=160ms、相机参考可信且epoch一致。故 stride>1 会使这类需要完整相邻源帧的量返回 source_time_or_frame_gap，即便一些通用特征仍可算。见 `source_alignment.py:62`、`:84`。

要求事件身份在 snapshot 中无冲突，person与sequence对应，诊断明确 identity_ambiguous=False、source_track_switch_candidate_count=0；有 primary timeline 时逐帧核对选择状态、逻辑人ID、源track、timestamp，事件总体诊断不能盖过窗口内实际冲突。硬质量失败用既有 quality_policy，候选jump/swap本身不自动升级成确认失败。见 `:102`、`:178`、`:198`。

具体四组输出：

- **FS01-M02**：独立找 takeoff 前的髋y向下再向上转折，每侧至少两个同向实测变化，允许平量化平台但不跨缺点；取最深且起点有前序观察支持的转折。得到预加载髋高下降量、左右膝屈曲变化、相邻观测身体速度变化率中位数。原事件 preload 不被改写。见 `:292`、`:356`。
- **FS01-M04**：landing_proxy 后“水平双踝间距 / 同帧二维髋宽”的中位数；不是此前 stance_width_body 的同义改名。髋宽退化或不足70%可测则拒绝。见 `:396`。
- **FS01-M05**：landing_proxy 到同视频同人下一个已观察 FS02 start 的时差，并给事件内落地候选后身体速度波动。见 `:417`。
- **FS09-M05**：stable_control_proxy 到输入中已有同人 FS10 或 FS02 start 的时差，以及稳定候选后速度波动。这个模块不会生成 FS10。见 `:421`、`:521`。

时差要求端点恰有源时间观察，并确认事件间每帧人物/相机/时间连续。找不到 successor 返回 not_observed，不以0或E代替缺证据。`SuccessorEventIndex` 先按 video/person/event_id 去冲突，再按video/person/code/time索引，二分找到最早候选；同一最早时刻多候选则 ambiguous。见 `events/associations.py:29`、`:49`。

## 14. 球轨迹与球拍可观测性：只读展示支路

`trajectory.py` 从既有 frames.jsonl 读取 ball/racket 记录，不修改推理、特征或评分输入。默认 observed sample_limit=240，预测400ms，最多8预测点；输入文件最大256MiB、200万行、50万观测点，参数也有限制。allow_partial 只允许忽略运行中文件末尾尚未完成的一行，不跳过中间损坏。见 `trajectory.py:28`、`:245`、`:929`。

简单主轨迹按观测数优先、平均confidence其次选一个track，按 processed_index 去重保留高置信度，同步输出覆盖率、最长缺失段、置信度统计。覆盖率描述“被观察到多少”，不是 precision/recall。见 `:376`、`:411`。

短时匀速预览取最后5段合法中心速度的中位数，段gap最多500ms；至少2点且有track身份才外推。输出点限制在画面0..1，裁剪会标 clamped_to_frame；请求最长可到5000ms，但仍最多8点并覆盖请求终点。默认400ms也只是启发式图像轨迹。见 `:443`、`:471`。

完整 reconstruction 会保留所有球候选轨迹：

1. 同track去重，删除低confidence且两侧都不合理的尖刺；无track的点各自独立，不放入一个“假身份”。见 `:549`、`:775`。
2. 普通相邻连接要求0<gap<=250ms且归一位置距离<=0.035+3×gap秒。见 `:540`。
3. 同ID250–600ms缺口只有双端运动支持才连接；跨ID最多300ms且方向更严格：两端与位移弦的余弦，同ID>=0.4、跨ID>=0.7；速度范围、速度比、外推端点误差也受限。见 `:596`。
4. 连接必须双向互选且无明显竞争；曾在同一帧同时出现的ID不能合并，传递A→B→C也不能绕过A/C同时出现约束。拥挤窗口超过64候选不做平方级关联。见 `:645`、`:678`、`:719`。
5. 短gap线性插值，受支持bridge用有界Hermite；每gap最多18补点，每点清楚标 observed/interpolated，单独保存插值时间区间。最终最多1000段、12000点，保端点均匀采样，但统计仍基于原观察。见 `:734`、`:766`、`:875`。

所有球段的 active_ball_identity 仍 unconfirmed。球拍只输出通用bbox轨迹，明确 association_status=unassociated，因为帧契约没有把球拍绑定到主运动员；没有拍头/拍柄/拍面/甜区关键点，也不输出球拍预测。见 `trajectory.py:854`、`:1016`。

因此这里没有实际球速（m/s）、球旋转、球落点、网球飞行物理模型，也没有从轨迹插值点推导击球事件。限制随payload一起输出，见 `:917`、`:1037`。

## 15. 视频、骨架播放、预览图分别是什么

### 15.1 文件名 annotated 不代表当前有覆盖层

`render.annotate_frame` 现在默认 show_detections=False、show_pose=False、show_status=False，直接返回原图copy；主流程未传这些开关。所以默认 `annotated.mp4`、`preview.jpg` 都是干净画面，而不是带检测框/骨架的渲染。见 `render.py:29`、`pipeline.py:735`。

诊断开关开启后，检测绘制只画confidence>=0.6的球/拍中心点及ID、不画person框；Pose只画confidence>=0.5的点和COCO前17躯干四肢连线；状态文本硬写 pose=RTMPose，这是诊断文字，不应作为实际后端来源，实际后端应读 metadata。court 参数当前没有绘制用途。见 `render.py:48`、`:72`、`:100`。

### 15.2 原始 PTS 回放

逐帧阶段 OpenCV先以 `fps/stride` 写 mp4v；完成后 `export_source_replay` 从原视频重新选出 first..last、stride 对应帧，`setpts=PTS-STARTPTS`、fps_mode=vfr、源time_base、禁B帧重排，输出H.264/yuv420p+faststart，无音轨。成功临时文件替换 annotated.mp4，保留真实选中帧间距。零packet duration补1个timebase tick以保留末帧。见 `pipeline.py:610`、`:801`、`source_replay.py:9`。

找不到ffmpeg或编码失败，不覆盖原文件，转为已有OpenCV文件的best-effort H.264转码；仍明确 timing_preserved=False 和恒定FPS可能不匹配源时序的warning。ffmpeg只从PATH或已安装imageio-ffmpeg取，不自动下载。见 `pipeline.py:90`、`:107`、`:809`。

### 15.3 独立骨架播放 payload

`build_pose_playback` 从 frames 和可用的 primary timeline 读取原图观测，给前端叠加，完全不使用测量的相机补偿/身体归一化坐标。最多10秒窗、600返回帧、骨架显示寿命100ms，confidence至少0.35；缺失点、越界点、冲突同名点、非法confidence不绘制，不插值。见 `pose_playback.py:18`、`:109`、`:167`。

有主时间线时必须精确匹配 processed_index/source_frame/timestamp且无身份歧义，只取该track唯一Pose；无时间线时只允许当前恰好一个可见Pose，不按最近/最大框臆选多人。轨迹换人、时间gap>100ms、processed索引跳跃会增加selection_epoch。旧Pose有效期最多到下一帧，下一帧缺失也会清除；分页时只带仍未过期且身份连续的前一个观察。见 `:145`、`:192`、`:209`、`:238`。

这个播放适配器限制输出 Halpe26命名子集，所以 WholeBody133的手/面细节点不会全部显示。它严格控制JSON重复key、非有限值、行/文件大小，支持运行中仅忽略不完整末行。见 `:28`、`:60`、`:73`。

## 16. 球场模块存在，但当前主流程不调用

`CourtDetector` 本身有 disabled、manual、自动线几何三种能力。manual visible_region 只给可见区域；只有声明四点为 court_outer_doubles_corners，才用四点到单位矩形的透视变换，并记录23.77m×10.97m双打场尺寸。见 `court.py:29`、`:47`。

自动分支在下部中央采样Lab主地面颜色，形态学+连通域提地面；CLAHE/Canny/Hough找到候选线，屏蔽人物，检查线中心与两侧亮度对比，按长度/亮度/对比筛选36线，再用凸包近似多边形。见 `court.py:95`、`:229`、`:253`、`:377`。

需足够线数、3%–85%合理区域、多方向、对比度、下部范围、无天空顶点、无近景人物大遮挡等检查，通过且confidence>=0.58才detected，否则uncertain；自动结果 calibration_usable始终False。见 `court.py:302`、`:322`、`:349`。

**当前 `run_pipeline` 并不创建 `CourtDetector`，而是第690–694行无条件写 `_disabled_court_observation()`。所以即便request写auto/manual，当前在线推理仍然关闭球场。** 相关配置/算法代码是可独立调用的保留实现，不是主链路正在提供的标定能力。summary也明确 court_detector/refresh_policy=disabled，见 `pipeline.py:75`、`:1082`。

## 17. 事件评估、模型分歧与校验

### 17.1 事件 schema

`validate_event_record` 检查ID、允许事件码、正向时间区间、置信度、相位时间、quality_flags、provenance；较新detector必须有正确事件局部运动覆盖率与运动参考来源，计数/比例/低覆盖flag必须互相一致。人工annotation走额外约束；有裁决状态时必须accepted。见 `events/schemas.py:26`、`:108`、`:182`、`:248`。

### 17.2 对人工真值的准确性评估

`evaluate_events` 默认Segment IoU>=0.5，按 canonical video_id（旧版可回退provenance.source_id）+event_code+person_track_id 分组，不允许跨视频、跨事件码、跨人匹配。组内做最小费用最大流：优先最大匹配数量，再最大总IoU，并用Fraction保持确定性。不是逐个贪心取最大IoU。见 `evaluation.py:37`、`:84`、`:144`、`:305`。

得到TP/FP/FN、precision/recall/F1、meanIoU、起止边界MAE/P95、相位边界MAE/P95与有效率，按事件码分组报告。有零真值时所有准确率指标为None，status=ground_truth_required，不把“没有对照”输出为100%或0%准确率。见 `evaluation.py:267`、`:394`、`:447`。

### 17.3 两个模型候选之间的差异

`compare_event_candidates` 是候选对候选，不用其中一个当真值；按事件码/人物组做保序动态规划，先匹配数再总IoU，报告左右匹配率、未匹配项、边界差（right−left）、多IoU阈值敏感性和内容hash。明确 accuracy_claim=False、ground_truth_provided=False，不输出F1/precision/recall。见 `disagreement.py:78`、`:271`、`:335`、`:353`。

### 17.4 整个运行的产物验证

单帧检查：schema、正画面尺寸、时间、检测track唯一、box/center有界、Pose必须引用同帧player、原生关键点数量/名称/ID映射一致、只有calibrated court可声明calibration_usable。见 `vision/validation.py:750`。

全运行检查：frames/summary必须存在，每行JSON有效，源帧号唯一、processed索引连续、时间严格递增、summary帧数匹配。之后核验主运动员、events、features、indicator-features、scores、feature-errors、scoring-loop-summary的一致关系；派生report核对summary及每个来源文件hash。见 `validation.py:283`、`:901`、`:59`。

这检查的是结构、一致性、证据绑定，不是识别准确率。通过validator也不能替代独立姿态/事件/教练评分真值。

## 18. 产物顺序、进度与完成语义

### 18.1 pipeline 把什么交给评分闭环

`run_minimum_scoring_loop` 是一个消费观测文件的明确边界。它不会重新调用检测或 Pose 模型。`pipeline.py:863` 传递的依赖包括：

| 参数组 | 具体内容 | 为什么需要 |
|---|---|---|
| 原始观测 | frames_path、primary_timeline_path | 唯一的测量输入与主运动员选择 |
| 输出与身份 | output_dir、source_id=job_id、稳定 video_id | 区分一次运行和被分析的视频内容 |
| 注册表 | 已预检的 registry 对象及 path | 由当前获授权指标要求决定要计算哪些特征 |
| 姿态来源 | pose_model=perception.pose_metadata() | 后端、原生拓扑、模型/配置 hash、版本绑定 |
| 内容证据 | video path/hash、frames path/hash、registry hash、lifecycle manifest/version/hash、calibration assets | 保持报告与实际输入及授权来源一致 |
| 评分准入 | calibrations、runtime binding registry、逐视频 view evidence | 生产等级必须在获授权的运行条件下应用 |
| 外部参考 | scoring_reference_context_path | 接受独立操作方/教练声明的参考，不从实际动作反推目标 |
| 进度 | report_scoring_progress | 将按事件/工作项的进度映射回整任务阶段 |

上述参数的构造见 `src/rallymate_vision/pipeline.py:864`、`:872`、`:880`、`:894`；loop 的签名与范围声明见 `src/rallymate_scoring/loop.py:184`。底层 loop 接口可以独立被实验或脚本调用，但 `run_pipeline` 的授权预检、路径匹配和防运行中替换是额外入口约束，不能因为 loop 可直接传对象就推断线上绕过授权。

闭环先由 registry 汇集每个 FS 事件所需的 measurement features 与 supplemental features，再读全量 frames/timeline，核验主球员算法版本并构建 `PoseSequence`。如有生产标定，再绑定当前模型版本、视频身份与视角证据；然后识别事件，为每个事件补充局部人物诊断，将诊断 quality_flags 合并到事件，先写 events.jsonl。见 `loop.py:223`、`:240`、`:267`、`:304`。因此特征不是对所有注册函数无差别计算，而是由本次注册表按事件选择。

每个事件变成包含边界、人物与 key_phases 的 `EventInterval`，调用 `compute_event_features`，补齐 video/event/person 与模型/来源 provenance。之后评分层将结果组合为指标记录并执行门控/评分（详见评分分篇），最后写 features、indicator-features、scores 和附加 source-aligned measurements。见 `loop.py:403`、`:416`、`:427`、`:602`。没有独立真值时，`evaluate_feature_errors(sequence, None, events, None, ...)` 仍生成 `event-feature-errors.json`，其意义是明确真值/误差评估缺失，而不是宣称误差为零，也不是运行异常日志。见 `loop.py:612`、`:618`。

闭环返回 `summary/events/indicator_records/scores/feasibility`，主 pipeline 直接消费这些已算结果生成 calculation-readiness、measurement-portfolio、scoring-cycle 三份派生报告，并为每份报告绑定 registry/events/indicator-features/scores 的文件 hash；这些派生报告没有重跑 Pose/事件/特征计算。见 `loop.py:837`，`pipeline.py:902`、`:922`、`:938`、`:951`。source-aligned 产物的路径与 hash 登记在闭环 summary 的 artifacts/artifact_sha256 中，见 `loop.py:812`、`:820`；不能只检查顶层 summary.artifacts 固定列表就断言它未生成。

### 18.2 顺序、文件与最终状态

逐帧结束后先 preparing_replay，再 tracking_player、recognizing_strokes、detecting_events、extracting_features、scoring、writing_results、finalizing、scoring_readiness。进度是按阶段/工作项推进：85–88回放、88–89人物、89–90挥拍、90–92事件、92–96特征、96–97测量整理、97–98保存、98–99报告、99–99.5校验；不按时间伪造完成。进度callback异常被吞掉，不让UI通知故障中止GPU推理。见 `progress.py:6`、`:19`、`pipeline.py:406`。

| 产物 | 写入阶段 | 含义 |
|---|---|---|
| frames.jsonl | 逐帧 | 原始观测与相机/质量证据 |
| annotated.mp4 / preview.jpg | 逐帧+回放导出 | 当前默认干净画面；mp4可保留源PTS |
| primary-player.jsonl | 离线身份选择 | 每采样帧逻辑主球员到原track映射 |
| primary-player-summary.json | 主球员诊断 | 全片覆盖/歧义/候选问题/先验 |
| events.jsonl | 评分闭环 | FS候选事件与相位 |
| features.jsonl | 特征提取 | 数值及原始/平滑系列证据 |
| indicator-features.jsonl | 指标聚合 | 指标对应测量与质量条件 |
| scores.jsonl | 评分阶段 | 分数或不允许评分的明确状态/原因 |
| source-aligned-measurements.json | 旧评分文件写完后 | 原文条款对齐的严格附加测量，技术分/等级为空 |
| event-feature-errors.json | 真值评估占位 | 特征误差评估状态；默认无真值，不能理解为运行异常日志 |
| scoring-loop-summary.json | 闭环汇总 | 版本、可行性、评分状态及产物hash |
| calculation-readiness.json | 派生汇总 | 指标计算准备情况 |
| indicator-measurement-portfolio.json | 派生汇总 | 已有/缺失测量组合 |
| scoring-cycle-measurement.json | 派生汇总 | 事件/动作周期测量 |
| scoring-loop-report.html | 闭环报告 | 闭环检查视图 |
| scoring-readiness.json | run验证后 | 整体评分准备度/姿态评估 |
| analysis-report.html | 最后报告 | 面向阅读的综合分析 |
| summary.json | 多次写入 | 最终全运行索引、输入、模型、耗时、质量、coverage、评分状态、产物 |

产物定义见 `pipeline.py:569`、`:817`、`:863`、`:922`、`:1149`、`:1189`、`:1197`、`:1216`。

summary的 `status=completed` 表示计算流程完成。`next_stage.ready` 只是 person/pose覆盖>=0.75、ball/racket覆盖>=0.5的观测准备门槛；虽然ready_for_hit_event_detection可能True，hit_event_detector_enabled仍为False。正式评分是否ready单独看result_state.scoring_status是否scored；当前球场禁用时场区/米制球场能力ready为False。见 `pipeline.py:990`、`:1119`。

实现层面的资源限制也需如实理解：推理是逐帧顺序执行，RTMPose还逐ROI调用；primary-player builder将整个JSONL读入内存，评分序列/证据也会占内存。轨迹/播放API读取有独立大小上限，但这不代表run_pipeline的整段离线后处理也具有同样的流式内存上限。见 `pipeline.py:633`、`pose/rtmpose_backend.py:129`、`primary_player.py:712`。

## 19. 如何沿一个特征反向追到源视频

以 `post_step_stance_width_body` 为例：

1. 从指标记录找到feature_name和event_id；该值的 `FeatureResult` 由 `event_features._fs01_fs02_event_summary_feature` 包装，见 `event_features.py:885`。
2. 在 `fs01_fs02_features.py:3032` 找到事件摘要分支，确认它消费 shared first_step_components 的 phase后双脚距离/body，而非整事件中位踝距。
3. 在 `:704` 查 phase锚点来源；在 `:2133` 查 phase对应哪一张源样本、误差是否<=160ms、是否legacy fallback。
4. 在 `:270` 查左右foot reference每帧来自细脚质心还是对齐ankle，以及置信度折扣。
5. 根据FeatureResult.source_frames与raw/smoothed证据timestamp，回到 `frames.jsonl` 同frame.index，再通过 `primary-player.jsonl` 的source_track_id确认取的是哪个人体Pose。
6. 依像素点/ROI/模型metadata/hash/原生拓扑追到推理来源；依camera_motion.matrix_to_reference及epoch复核测量坐标变换。
7. 在原始视频对应timestamp查看实际画面；骨架播放使用原图点，不能拿稳定化后的测量坐标直接覆盖视频。

这个链条也解释了为什么同一个“测得数字”可能在评分层仍不可用：其身份、phase、视角、细脚、事件真值、独立校准等条件是独立证据，任何一项缺失都不能靠数值本身补齐。

## 20. 模块文件索引与建议阅读顺序

| 文件 | 阅读重点 |
|---|---|
| vision/__main__.py、cli.py、__init__.py | CLI入口、错误/成功输出 |
| vision/contracts.py | 请求数据类、路径、默认值和预设一致性 |
| vision/pipeline.py | 全链路顺序、授权、逐帧、产物、完成语义 |
| vision/inference.py | 通用检测、小球补检、去重、Pose facade |
| vision/pose/base.py、registry.py | 后端协议与factory |
| vision/pose/adapters.py | ROI、人关联、坐标回映射 |
| vision/pose/yolo_backend.py、rtmpose_backend.py | 两种实际网络推理后端 |
| vision/pose/presets.py、metadata.py、data/keypoint_schemas.json、__init__.py | 预设、原生拓扑、公开导出 |
| vision/tracking.py | 每帧对象关联ID |
| vision/timebase.py、camera_motion.py | 真实时间、背景参考系 |
| tracking/primary_player.py、__init__.py | 离线主球员时间线与诊断 |
| features/schemas.py、coordinates.py、validity.py | 时序数组、等轴坐标、点可用性 |
| features/geometry.py、kinematics.py、smoothing.py、temporal.py | 基础数学与连续时间策略 |
| events/rules.py、schemas.py | 候选段、phase、输出约束 |
| features/event_features.py | 特征注册、分发、证据封装、相机窗 |
| features/fs01_fs02_features.py、fs09_features.py | 专项测量纯函数与汇总 |
| features/source_alignment.py | 原文条款对齐的补充严格测量 |
| events/associations.py | 同视频同人已有后继事件关联 |
| events/evaluation.py、disagreement.py、__init__.py | 真值评估、模型分歧、公开接口 |
| vision/quality.py | 灰度画质与元数据提示 |
| vision/trajectory.py、pose_playback.py | 只读前端展示数据 |
| vision/render.py、source_replay.py | 像素绘制、源PTS视频回放 |
| vision/court.py | 未接入当前主流程的球场几何算法 |
| vision/validation.py、progress.py、utils.py | 产物一致性、阶段进度、基础几何/设备工具 |
| features/__init__.py | 基础特征公开导出，不另开隐藏计算支路 |

推荐首先完整阅读 pipeline.py，再沿“模型→帧契约→主球员→坐标序列→事件→特征→评分交接→播放”的顺序阅读；不要先从最长的专项特征文件随机进入，否则很难知道其输入已经做过哪些坐标/相机/身份处理。

## 21. 源码与测试共同给出的边界（本次未运行测试）

以下测试名称和断言主题为阅读实现提供旁证，不表示本次测试执行结果：

- `tests/test_tracking.py:15` 检查球身份寿命按源时间；`:138` 检查人物更长漏检容忍。
- `tests/test_pose_backend.py:47` 检查ROI回映射契约；`:87` 检查32px默认ROI保护；`:136` 检查133点分组。
- `tests/test_primary_player.py:75` 检查0.20选择与测量门槛分离；`:177` 检查人物并列时检测顺序不影响结果且报告歧义；`:290` 检查逻辑主球员跨原track碎片。
- `tests/test_camera_compensated_coordinates.py:41` 检查平移/旋转/尺度参考几何；`:59` 检查非法矩阵失败关闭；`:77` 检查参考点不被裁到当前画面。
- `tests/test_camera_guard_event_boundaries.py:52` 检查平滑不跨换镜头；`:76` 检查跨镜头人工事件整体拒绝。
- `tests/test_events.py:170` 检查静止Pose不制造事件；`:174` 检查肩缺失可用直接髋事件参考；`:113` 检查每个派生事件独立覆盖率；`:356` 检查无真值不伪装满分；`:380` 检查全局匹配优先匹配数量。
- `tests/test_features.py:90` 检查导数用不规则timestamp；`:134` 检查缺点非0且短gap可平滑；`:210` 检查高置信越界点仍缺失。
- `tests/test_trajectory.py:259` 检查双端运动支持bridge；`:331` 检查传递关联不能合并同时出现的不同球；`:378` 检查采样仍保留插值区间来源。
- `tests/test_pose_playback.py:76` 检查精确主身份选择；`:112` 检查缺Pose/长gap清除旧显示；`:144` 检查不可靠时间不借上一帧点。
- `tests/test_source_replay.py:94` 覆盖真实VFR片段与stride后所有选中PTS（含末帧）的保留。

当前最容易误读的实现事实是：模型更大/点更多不等于正式评分可用；函数名annotated不等于输出有覆盖；court配置存在不等于主流程启用；track_id不是身份真值；FS候选不是确认接触；二维身体单位不是米制；测量值0不是缺失；结构校验通过不是精度验证通过。
