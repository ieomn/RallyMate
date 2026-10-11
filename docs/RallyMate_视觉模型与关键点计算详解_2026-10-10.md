# RallyMate 视觉模型与关键点计算详解

RallyMate 将网球视频变成可回放核对的动作测量：先检测球员、球和球拍，再提取同一球员的身体关键点，利用连续坐标计算角度、距离、速度与动作阶段，最后形成测量报告和评分证据。

这份手册重点解释**视觉模型怎样工作、关键点怎样提取、坐标怎样变成指标**。接口调用方式另见 [RallyMate API 接口详解](RallyMate_API接口详解_2026-10-09.html)。实现依据为 2026 年 10 月 10 日的本地源码；文中的数值演算均明确标为教学示例。

当前默认姿态方案为 **RTMPose-M / Halpe26 / PyTorch**。网页百分制是**测量证据参考分**；自动技术等级仍需要教练标定。模型预设名称中的 `realtime` 是运行配置名称，当前上传视频链路还包含排队、解码与后处理，不能据此承诺端到端实时帧率。

## 先用一条链路理解整个系统

<ol class="pipeline-diagram" aria-label="从视频到动作测量的六步流程">
<li><span>01</span><b>读取视频</b><small>源帧号 · 时间戳</small></li>
<li><span>02</span><b>YOLO 检测</b><small>球员 · 球 · 球拍</small></li>
<li><span>03</span><b>RTMPose 姿态</b><small>人物 ROI · 26 点</small></li>
<li><span>04</span><b>整理时序</b><small>身份 · 坐标 · 质量</small></li>
<li><span>05</span><b>计算动作指标</b><small>角度 · 距离 · 速度</small></li>
<li><span>06</span><b>形成报告</b><small>测量 · 证据 · 回放</small></li>
</ol>

| 环节 | 输入 | 输出 | 主要实现 |
| --- | --- | --- | --- |
| 目标检测 | 一帧视频图像 | 人、球、球拍的框、类别和检测分值 | YOLO 推理适配器 |
| 目标跟踪 | 当前检测与历史轨迹 | 跨帧目标编号 `track_id` | 项目跟踪逻辑 |
| 人体姿态 | 人物裁剪区域 | 26 点二维坐标及逐点置信度 | RTMPose 与 MMPose |
| 主球员和时序整理 | 每帧骨架、目标编号、源时间 | 同一个人的有效动作序列 | 主球员选择、时间与质量处理 |
| 动作测量 | 连续关键点及候选时间段 | 屈膝、站距、移动、肩髋变化等 | 几何、运动学与时序规则 |
| 结果与评价 | 指标值、覆盖、证据、回放位置 | 参考分、缺测原因、人工评审入口 | 测量评价和报告模块 |

**神经网络完成的是图像中的定位。** 关键点之间的几何运算、动作候选识别、证据汇总和参考分，由后续代码完成。这些环节各自可能出现误差，换一个视觉模型后需要逐层验证。

主循环依次调用检测、跟踪和姿态估计，并在帧处理完成后生成主球员、挥拍和脚步分析产物。参见 [逐帧处理](C:/Users/Admin/Documents/网球/src/rallymate_vision/pipeline.py:658) 与 [主球员及后处理](C:/Users/Admin/Documents/网球/src/rallymate_vision/pipeline.py:821)。

## 开会时可以直接使用的说明

> 我们先用 YOLO 找到球员、球和球拍，跟踪球员身份，再把人物区域交给 RTMPose 提取带置信度的关键点。关键点还原到原视频后，按时间连接成同一个人的动作序列。我们用这些点计算屈膝、站距、髋部移动、手腕速度和肩髋二维变化，并在动作片段里汇总。当前参考分主要评价测量证据，正式技术评分需要教练标定。因此评估新模型时，我们会分别看定位精度、时序稳定性、动作测量误差和教练评价一致性。

## 视觉提取：从视频到可追溯的关键点

这一层的输入是视频帧，输出是每帧的目标框、类别、跟踪编号和人体关键点，同时保留原视频时间戳。整条链路是：**读取原视频 → YOLO 找人、球、球拍 → 跨帧关联目标 → 裁剪人物区域 → RTMPose 提取骨架 → 恢复原图坐标 → 选择主球员时间线**。后面的动作判断和评分读取这些结构化证据。

依据：[视觉主流程](C:/Users/Admin/Documents/网球/src/rallymate_vision/pipeline.py:654)、[每帧输出结构](C:/Users/Admin/Documents/网球/src/rallymate_vision/pipeline.py:703)。

### 1. 当前默认配置是什么

按截至 2026 年 10 月 10 日的服务源码，检测模型默认文件为 `models/yolo26n.pt`；服务构造请求时使用 `detect_imgsz=960`、检测置信度阈值 `0.15`。姿态默认预设为 `rtmpose-m-halpe26-online`，对应 RTMPose-M、PyTorch、`realtime` 配置和 **256×192（高×宽）** 输入。

请求中还保留通用字段 `pose_imgsz=640`、`pose_confidence=0.25`，但 RTMPose 的实际输入尺寸由其 MMPose 配置决定，不能据此称其输入为 640×640。RTMPose 后端也不会按该通用 `pose_confidence` 参数删除关键点；点是否可用由后续规则判断。服务默认 `frame_stride=1`，默认最多提取 2 名球员的姿态，接口允许设置为 1～4。

以上是**代码默认值**，不是当前进程配置证明，也不是速度或精度实测结果；环境变量和具体任务可改变部分设置。应以任务请求、模型元数据和运行摘要核对实际使用模型。

依据：[服务模型默认值](C:/Users/Admin/Documents/网球/src/rallymate_service/config.py:399)、[服务请求参数](C:/Users/Admin/Documents/网球/src/rallymate_service/api.py:875)、[人数限制](C:/Users/Admin/Documents/网球/src/rallymate_service/api.py:784)、[默认姿态预设](C:/Users/Admin/Documents/网球/models/rtmpose/deployment-presets.json:4)、[RTMPose 输入配置](C:/Users/Admin/Documents/网球/src/rallymate_vision/pose/rtmpose_backend.py:95)。

### 2. YOLO 检测与跨帧跟踪各做什么

YOLO 输入整帧图像，返回目标类别、置信度和检测框。适配层把 `person`、`sports ball`、`tennis racket` 统一命名为 `player`、`ball`、`racket`，保存原图像素框、归一化框和中心点。人体姿态只对球员框执行，球和球拍保留自己的检测结果。

源码还包含高清小球补检：原视频长边至少 1600 像素、基础输入小于 1536 时，可用 1536 尺度只检测球，补检置信度至少 0.25，再筛选小目标、合并重叠观察。它是一次额外模型检测，并不等于在漏检帧中生成真实球位置。

跟踪由 `SimpleMultiClassTracker` 完成：同类别目标按框重叠度和中心距离关联，代价为 `0.65×(1−IoU)+0.35×中心距离`，然后分配 `track_id`。当前实现没有调用 ByteTrack 或 BoT-SORT。编号表示跨帧关联结果，不能保证遮挡后永不换号；有时间戳时，球轨迹关联的默认最长间隔为 300ms。

依据：[检测和小球补检](C:/Users/Admin/Documents/网球/src/rallymate_vision/inference.py:97)、[类别与框输出](C:/Users/Admin/Documents/网球/src/rallymate_vision/inference.py:127)、[跟踪器](C:/Users/Admin/Documents/网球/src/rallymate_vision/tracking.py:17)、[关联代价](C:/Users/Admin/Documents/网球/src/rallymate_vision/tracking.py:81)。

### 3. 时间轴怎样保留

每帧同时记录原视频帧号 `index`、处理序号 `processed_index`、毫秒时间 `timestamp_ms` 和时间来源 `timestamp_source`。优先读取解码器报告的播放时间并以视频起点归零；若时间无效或不递增，才用原帧号和平均 FPS 推算，并明确标为 `fps_fallback`。

即使设置跳帧，原帧号和时间戳仍被保留，不能把处理序号直接当作原视频时间。时间回退帧还会使相机运动验证不可用，供下游识别不可靠的时间依据。保存的标注视频播放帧率也不能替代这些源时间戳。

依据：[视频时钟](C:/Users/Admin/Documents/网球/src/rallymate_vision/timebase.py:19)、[读取与跳帧](C:/Users/Admin/Documents/网球/src/rallymate_vision/pipeline.py:633)、[时间回退处理](C:/Users/Admin/Documents/网球/src/rallymate_vision/pipeline.py:674)。

### 4. 人物 ROI 如何进入 RTMPose

姿态适配器按球员检测框面积从大到小排序，选取最多 `max_players` 个框。每个框的左右各增加原框宽度的 15%，上下各增加原框高度的 15%，再限制在图像边界内；因此完整未截断的框宽高各变为原来的 1.3 倍。扩大区域为人体周边留下上下文。扩大后宽或高不足 32 像素时跳过该 ROI。

RTMPose 对每块裁剪图调用 `inference_topdown`，得到当前 ROI 中的 `keypoints_xy` 和逐点 `keypoint_scores`；Halpe26 分别为 26×2 和 26 项。后端把逐点分数均值作为整个人体姿态的 `pose_score`。这里是单张 ROI 的二维姿态推理，后端不使用传入时间戳进行时序建模；轨迹和动作时序由外层处理。默认 `realtime` 不启用翻转测试，`analysis` 配置会启用。

依据：[ROI 选择和裁剪](C:/Users/Admin/Documents/网球/src/rallymate_vision/pose/adapters.py:39)、[扩大框实现](C:/Users/Admin/Documents/网球/src/rallymate_vision/utils.py:61)、[后端推理输出](C:/Users/Admin/Documents/网球/src/rallymate_vision/pose/rtmpose_backend.py:120)。

### 5. 如何恢复原图坐标：一个手算例子

后端返回的是 ROI 坐标系中的关键点。适配器加上裁剪框左上角偏移，得到 `x_px/y_px`，再分别除以原图宽高保存 `x_normalized/y_normalized`。原点位于图像左上角，x 向右、y 向下。

**示意例子，非真实检测结果：**原图 1920×1080，人物框为 `[100,200,300,600]`，宽 200、高 400。每侧扩 15% 后，裁剪框为 `[70,140,330,660]`。若 RTMPose 返回某膝点 ROI 坐标 `(90,180)`，原图位置就是 `(90+70,180+140)=(160,320)`，归一化位置为 `(160/1920,320/1080)≈(0.083333,0.296296)`。

如输出超出原图，序列化坐标会限制到边界，同时保留原始坐标并标记 `in_frame=false`，供下游排除。输出还携带 `person_track_id`、ROI 框、关键点格式和逐点置信度。归一化坐标用于存储与显示，后续几何计算有独立坐标约定。

这里相加的坐标已经由后端解码到裁剪图空间，不是神经网络输入画布上的原始坐标。因此业务适配器无需再按 256×192 手动缩放一次；重复缩放会把关键点放到错误位置。逐点置信度也应保留，不能用整个人体的平均分替代每个关节的可见性判断。

依据：[坐标恢复与越界标记](C:/Users/Admin/Documents/网球/src/rallymate_vision/pose/adapters.py:85)、[姿态输出关联](C:/Users/Admin/Documents/网球/src/rallymate_vision/pose/adapters.py:124)。

<div class="diagram-grid">
<figure><svg viewBox="0 0 330 290" role="img" aria-labelledby="roi-title roi-desc">
<title id="roi-title">人物区域与原视频坐标的关系</title><desc id="roi-desc">示意人物裁剪左上角偏移为70、140，区域内点90、180，还原后为160、320。图形不按像素比例绘制。</desc>
<rect x="14" y="18" width="302" height="207" fill="#f8fafc" stroke="#aebcc9"/>
<text x="26" y="42">原视频坐标系</text>
<rect x="83" y="64" width="113" height="140" fill="#eaf2fa" stroke="#1756a0" stroke-dasharray="5 4"/>
<text x="205" y="78">人物 ROI</text>
<path d="M 83 64 L 126 64 L 126 126" fill="none" stroke="#617084" stroke-width="1.5"/>
<circle cx="83" cy="64" r="4" fill="#617084"/><circle cx="126" cy="126" r="5" fill="#1756a0"/>
<text x="28" y="245">裁剪起点 (70, 140)</text><text x="28" y="267">ROI 内点 (90, 180) → 原图 (160, 320)</text>
<text x="135" y="128">膝点</text><text x="98" y="193">坐标还原示意</text>
</svg><figcaption>先撤销网络预处理变换，再加裁剪偏移。数值为教学示例，图形不按像素比例绘制。</figcaption></figure>
<figure><svg viewBox="0 0 330 290" role="img" aria-labelledby="knee-title knee-desc">
<title id="knee-title">髋膝踝三点计算屈膝角</title><desc id="knee-desc">髋膝踝构成150度内角，屈曲角为180减150等于30度。仅是二维几何演算，不代表正确动作标准。</desc>
<path d="M 153 37 L 153 148 L 208.5 244.1" fill="none" stroke="#1756a0" stroke-width="4"/>
<path d="M 153 112 A 36 36 0 0 1 171 179.2" fill="none" stroke="#617084" stroke-width="1.5"/>
<g fill="#1756a0"><circle cx="153" cy="37" r="6"/><circle cx="153" cy="148" r="6"/><circle cx="208.5" cy="244.1" r="6"/></g>
<text x="170" y="43">髋</text><text x="112" y="152">膝</text><text x="225" y="249">踝</text><text x="192" y="144">内角 150°</text>
<text x="29" y="274">屈曲角 = 180° − 150° = 30°</text>
</svg><figcaption>关键点提供位置，几何函数算出角度；是否符合技术要求，还需要相应评价标准。</figcaption></figure>
</div>

### 6. Halpe26 的顺序与兼容关系

索引 **0～16** 与旧 COCO17 顺序一致：鼻、左眼、右眼、左耳、右耳、左肩、右肩、左肘、右肘、左腕、右腕、左髋、右髋、左膝、右膝、左踝、右踝。索引 **17～25** 依次为 `head`、`neck`、`hip`、`left_big_toe`、`right_big_toe`、`left_small_toe`、`right_small_toe`、`left_heel`、`right_heel`。

旧 17 点保留业务 `downstream_joint_id`；新增 9 点该字段为 `null`，但仍按名称完整保存。`null` 表示尚无旧编号映射，不等于丢弃或不可使用。脚跟、大小脚趾能够为足部参考位置提供额外观察。不同模型拓扑必须按声明的格式读取，不能把所有模型的“第 20 点”当作同一部位，也不能认为 26 个输出点都直接参与每一项评分。

依据：[COCO17 与 Halpe26 字典](C:/Users/Admin/Documents/网球/src/rallymate_vision/pose/data/keypoint_schemas.json:4)、[Halpe26 完整定义](C:/Users/Admin/Documents/网球/src/rallymate_vision/pose/data/keypoint_schemas.json:27)、[足部参考入口](C:/Users/Admin/Documents/网球/src/rallymate_features/fs01_fs02_features.py:270)。

### 7. 如何固定分析对象，以及模型能力的边界

主球员不是简单取每帧最大框。主球员模块综合轨迹覆盖率、关键点可见比例、人物面积与活动程度，结合逐帧检测质量和身份连续性，在一段视频中选择路径。输出 `primary-player.jsonl`，把业务主球员与各帧 `source_track_id` 连接起来，并记录缺失、竞争身份、编号切换及关键点跳变等诊断。此处选择分数是工程启发式，不是身份识别准确率。

模型原生观察包括目标框、类别、关键点二维位置及置信度；跟踪编号、主球员选择、坐标转换、后续阶段和技术解释属于业务程序。RTMPose 没有直接输出“动作正确”“已经触球”或“脚在承重”。讨论替换模型时，应逐项检查其能否保持这些输出、坐标和时间约定，再检验业务结果是否一致。

依据：[主球员先验](C:/Users/Admin/Documents/网球/src/rallymate_tracking/primary_player.py:194)、[路径选择](C:/Users/Admin/Documents/网球/src/rallymate_tracking/primary_player.py:249)、[诊断含义](C:/Users/Admin/Documents/网球/src/rallymate_tracking/primary_player.py:697)。

## RTMPose 内部怎样从图像找出关键点

人物裁剪解决“看哪一个人”，姿态网络解决“这个人的肩、肘、腕、髋、膝、踝分别在哪里”。当前后端加载已配置的模型文件进行推理；一次视频分析不会现场重新训练网络。

### 图像预处理与特征提取

本机使用的模型配置将输入尺寸写成 `(192, 256)`，顺序是**宽、高**；部署注册表写成 `[256, 192]`，字段含义是**高、宽**，两者指向同一规格。项目先裁人物 ROI，再以 `bboxes=None` 传给 MMPose。MMPose 把整块 ROI 当作输入框，经默认 `padding=1.25` 的中心尺度处理及宽高比调整，执行 `TopdownAffine`；随后做 BGR 转 RGB 和按配置均值、标准差的图像归一化。复现时不能只做一次简单 resize。

参见 [整块 ROI 作为输入框](C:/Users/Admin/Documents/网球/runtime/rtmpose/.venv/Lib/site-packages/mmpose/apis/inference.py:160) 与 [中心尺度及默认 padding](C:/Users/Admin/Documents/网球/runtime/rtmpose/.venv/Lib/site-packages/mmpose/datasets/transforms/common_transforms.py:52)。项目的每侧 15% 外扩与 MMPose 的预处理是两个不同步骤。

网络主干为 **CSPNeXt**。它将图像转换为特征，再交给 **RTMCCHead** 输出 26 个关键点的位置表示。这里的“特征”是网络内部数组，与后文“膝角、站距等业务特征”不是同一层数据。

参见本机安装的 [RTMPose 配置](C:/Users/Admin/Documents/网球/runtime/rtmpose/.venv/Lib/site-packages/mmpose/.mim/configs/body_2d_keypoint/rtmpose/body8/rtmpose-m_8xb512-700e_body8-halpe26-256x192.py:52) 和 [验证时预处理流程](C:/Users/Admin/Documents/网球/runtime/rtmpose/.venv/Lib/site-packages/mmpose/.mim/configs/body_2d_keypoint/rtmpose/body8/rtmpose-m_8xb512-700e_body8-halpe26-256x192.py:153)。这些是依赖包配置，不代表本项目已经完成网球专项训练。

### SimCC 将横坐标和纵坐标分别预测

当前关键点头采用 SimCC 表示。可以把它理解为：对每个关节，网络分别给出“可能位于哪个横向位置”和“可能位于哪个纵向位置”的一维得分序列，再解码得到 `(x, y)`。

配置中 `simcc_split_ratio = 2.0`。因此宽 192、高 256 的网络输入对应长度为 **384 和 512** 的横、纵位置表示。解码器取横、纵得分峰值位置，再除以 2，恢复网络输入坐标；这不代表在原视频中获得了固定的半像素测量精度。

简化后的计算关系为：

```text
人物图像 → CSPNeXt 特征 → RTMCCHead
                           ├─ x 方向得分序列 → 峰值位置 / 2
                           └─ y 方向得分序列 → 峰值位置 / 2
                         → 每个关节的二维坐标与分值
```

实际前向计算还包含卷积、展平、MLP 和 GAU 模块；上图只保留坐标预测主线。参见 [RTMCCHead.forward](C:/Users/Admin/Documents/网球/runtime/rtmpose/.venv/Lib/site-packages/mmpose/models/heads/coord_cls_heads/rtmcc_head.py:134) 与 [SimCC 解码](C:/Users/Admin/Documents/网球/runtime/rtmpose/.venv/Lib/site-packages/mmpose/codecs/simcc_label.py:174)。

### 先还原人物区域坐标，再还原原视频坐标

坐标经过两次恢复：MMPose 将网络输入坐标映射回传入的人物区域；项目适配器再加上人物裁剪的左上角偏移，得到原视频坐标。因此不能把网络的 192×256 坐标直接加上原图偏移，也不能把已还原的坐标再缩放一次。

MMPose 返回的 `pred_instances.keypoints` 已处于传入区域的坐标系。后端检查坐标形状为 `(26, 2)`、分值形状为 `(26,)`，再交给项目适配器。默认解码不做 softmax，逐点原始分值取 `min(max(x方向得分), max(y方向得分))`。项目将逐点分值截到 `[0,1]`，超出时保留 `raw_confidence` 和异常标记；整体姿态 `pose_score` 则先对后端原始逐点分值取均值，再单独截到 `[0,1]`。不能把它描述成已经校准的概率，或误认为一定等于序列化后逐点分值的均值。

参见 [MMPose 坐标恢复](C:/Users/Admin/Documents/网球/runtime/rtmpose/.venv/Lib/site-packages/mmpose/models/pose_estimators/topdown.py:150)、[原始分值计算](C:/Users/Admin/Documents/网球/runtime/rtmpose/.venv/Lib/site-packages/mmpose/codecs/utils/post_processing.py:90)、[项目后端解包](C:/Users/Admin/Documents/网球/src/rallymate_vision/pose/rtmpose_backend.py:123) 和 [原视频偏移恢复](C:/Users/Admin/Documents/网球/src/rallymate_vision/pose/adapters.py:90)。

**置信度用于判断预测是否适合继续计算；它没有直接等同于真实准确率。** 即使某个关节的分值较高，也需要用人工位置标注核对其定位误差。

## 关键点如何变成可计算的动作特征

关键点模型提供逐帧坐标与置信度；动作测量层再按人物、时间、动作片段计算几何和运动学特征。模型识别出“膝盖在哪”，并不会直接得到“屈膝是否正确”。每个特征同时保留数值、单位、有效状态、置信度和证据帧。

### 1. 先统一坐标，再比较动作

图像坐标的 x 向右、y 向下。计算几何时，两轴都除以同一个长边长度 L=max(宽,高)：p=(x/L,y/L)。不能把 x 除宽、y 除高后的存储坐标直接用于欧氏距离或角度，否则宽高比会改变几何关系。存在通过校验的相机补偿时，先把当前像素变换到参考画面，再除以 L；这仍是二维图像测量。

步伐通用测量中的身体尺度不是身高。逐帧计算肩宽、髋宽、肩中心到髋中心距离，对有效值取中位数；动作片段内再取这些逐帧尺度的中位数 S。相应距离除以 S 后得到 body，速度对应 body/s，目的是减轻人物远近和图像尺寸的影响，并不换算成米。挥拍链采用躯干尺度等独立约定，不能把所有归一化数值混用。

来源：[坐标与相机补偿](C:/Users/Admin/Documents/网球/src/rallymate_features/coordinates.py:56)、[身体尺度](C:/Users/Admin/Documents/网球/src/rallymate_features/coordinates.py:268)、[片段尺度](C:/Users/Admin/Documents/网球/src/rallymate_features/event_features.py:355)。

### 2. 常用几何公式与含义

设左右肩为 SL、SR，左右髋为 HL、HR，左右踝为 AL、AR：

- 肩中心 C肩=(SL+SR)/2；髋中心 C髋=(HL+HR)/2。
- 身体中心 C身=(C肩+C髋)/2。它与髋中心是两个量，都只是视觉代理点，不是测得的生物力学质心。
- 膝内角 θ=acos[((H−K)·(A−K))/(|H−K|·|A−K|)]；屈曲角=180°−θ。腿越直，屈曲角越接近 0°。
- 基础站距=|AR−AL|/S；髋相对双踝高度=[(ALy+ARy)/2−C髋y]/S。图像 y 向下，因此该高度用“踝 y 减髋 y”。
- 髋相对双踝支撑的位置比例 r=[(C髋−AL)·(AR−AL)]/|AR−AL|²；r=0、1 分别对应左右踝，0.5 对应中点。它是沿双踝连线的投影，不是实际足底压力中心。
- 躯干倾斜=atan2(C肩x−C髋x, −(C肩y−C髋y))，转成度，表示相对画面竖直方向的带符号倾斜。
- 肩线、髋线分别由左右端点求图像朝向，再求轴线夹角差、处理角度跨界，平滑后求角速度。这是二维肩髋投影变化，不能直接称为真实三维转髋角。

逐帧曲线还要按特征定义汇总成片段结果，例如中位数、峰值、带符号最大偏差，方向角使用圆均值；不是统一挑一张“最好看的截图”。普通特征置信度由必需关节的置信度均值乘有效帧比例得到，部分专门特征再应用自身规则。该置信度表示观测支持程度，不是教练判断正确的概率。

来源：[中心定义](C:/Users/Admin/Documents/网球/src/rallymate_features/coordinates.py:251)、[三点角度](C:/Users/Admin/Documents/网球/src/rallymate_features/geometry.py:6)、[基础特征](C:/Users/Admin/Documents/网球/src/rallymate_features/event_features.py:475)、[肩髋角速度](C:/Users/Admin/Documents/网球/src/rallymate_features/event_features.py:535)、[特征置信度](C:/Users/Admin/Documents/网球/src/rallymate_features/event_features.py:397)、[片段汇总](C:/Users/Admin/Documents/网球/src/rallymate_features/event_features.py:451)。

### 3. 时间序列与脚部细点

速度依据真实 timestamp_ms 求导，时间先换成秒：v≈(p右−p左)/(t右−t左)，速率为向量长度；加速度再对速率求导。代码按有效邻点差分、端点使用单侧差分，因此不能把它口头简化为“每隔固定帧数算一次”。部分序列先平滑，且动作、身份和相机参考窗口还要单独检查；现有基础差分本身不能证明跨缺帧的连续运动。

FS01/FS02 的脚部参考点优先使用脚跟、大脚趾、小脚趾的三点均值。细点不可用时，允许显式踝点回退：若该拓扑存在细点，先利用同时有效的帧估计“细点中心−踝点”的中位偏移，回退时加上偏移；没有重叠证据就不强行对齐。原本只有踝点的拓扑可使用踝点。回退贡献带 0.65 置信度系数，并记录来源。脚的低速或上移只说明图像运动，不能直接证明离地、触地、支撑力或蹬地力。

**虚构计算例 1，仅解释公式：** 假设片段尺度等价于 100 像素，双踝间距 140 像素，则站距为 1.4 body。某帧髋(500,400)、膝(500,500)、踝(600,600)，膝内角为 135°，屈曲为 45°。假设平滑后的髋中心 0.2 秒移动 50 像素，则该段平均速度为 2.5 body/s。这些数值本身不对应任何现行技术等级。

来源：[时间戳差分](C:/Users/Admin/Documents/网球/src/rallymate_features/kinematics.py:57)、[脚部参考及回退](C:/Users/Admin/Documents/网球/src/rallymate_features/fs01_fs02_features.py:270)、[回退系数](C:/Users/Admin/Documents/网球/src/rallymate_features/fs01_fs02_features.py:18)。

## 片段如何对应到 13 项指标

当前步伐闭环覆盖 FS01 分腿垫步、FS02 第一步启动、FS09 制动急停/稳定。下表名称来自当前指标卡；最后一列概括当前测量映射，不能把名称中的“离地、落地、发力”理解为传感器已经直接测到了这些事实。

| 指标 | 当前名称 | 主要计算证据 |
|---|---|---|
| FS01-M02 | 重心预加载 | 髋高度、膝屈曲、站距、髋与双踝支撑位置 |
| FS01-M03 | 双脚轻微离地 | 双脚参考点上移幅度、同步时差、持续时间、髋垂直速度 |
| FS01-M04 | 双脚分开落地 | 双脚垂直减速时差、减速后脚距、髋横向波动 |
| FS01-M05 | 重心重新分配并准备启动 | 身体中心速度、髋与双踝位置、躯干倾斜、肩髋角速度 |
| FS02-M02 | 重心向来球方向转换 | 启动方向、目标方向偏差及姿态；需要独立目标方向 |
| FS02-M03 | 支撑脚发力 | 膝伸展速度、启动方向加速度、脚部动作时差等代理量 |
| FS02-M04 | 启动脚离地 | 启动侧、脚参考点峰值速度、相对位移、持续时间 |
| FS02-M05 | 第一步落地并建立移动方向 | 脚速下降、第一步位移、随后髋方向一致性、脚距 |
| FS09-M01 | 判断身体惯性方向 | 髋速度、方向、相对双踝偏移、速度趋势 |
| FS09-M02 | 制动脚落地 | 左右踝减速、制动侧、膝屈曲变化、髋减速及时差 |
| FS09-M03 | 下肢吸收身体惯性 | 髋降速、膝屈曲变化、髋高度变化、躯干波动 |
| FS09-M04 | 重心重新稳定 | 髋与双踝关系、降速、站距、稳定及低踝速持续时间 |
| FS09-M05 | 身体进入稳定控制状态 | 稳定持续、降速、躯干波动、肩髋角速度变化及时序 |

挥拍使用另一条时序链：准备→加速挥拍→随挥，依据平滑手腕速度和观察到的减速形成阶段；同时计算手腕路径、峰值速度、肘角变化等。挥拍峰值不是已确认触球点，不能与上述三类步伐阶段混为同一套标签。

### 动作开始和结束怎样确定

步伐规则先从身体运动速度与局部活动阈值找活动区间，再结合持续位移、路径效率和方向一致性筛选。每个区间周围分别寻找启动前、活动中、减速后窗口，生成 FS01、FS02、FS09 候选；内部阶段还会观察脚部上升/下降活动、髋部变化、膝伸展和速度回落。它们是二维运动代理，模型没有直接观察足底接触或受力。

挥拍阶段规则先寻找手腕运动峰值，再回看加速段。当前基线用局部峰值速度的 35% 帮助定位加速起点和后续低速段；随挥结束还要求观察到持续至少 100 ms 的低速，不能因为视频被截断就补出完整随挥。这些数值是阶段估计的工程规则，不是教练评分阈值。

参见 [步伐活动区间](C:/Users/Admin/Documents/网球/src/rallymate_events/rules.py:932)、[三类候选窗口](C:/Users/Admin/Documents/网球/src/rallymate_events/rules.py:1310) 与 [挥拍阶段规则](C:/Users/Admin/Documents/网球/src/rallymate_scoring/temporal_recognition.py:245)。

来源：[13 项范围](C:/Users/Admin/Documents/网球/src/rallymate_scoring/feasibility.py:12)、[当前指标卡](C:/Users/Admin/Documents/网球/src/rallymate_scoring/data/metric_cards.json:1)、[指标特征映射](C:/Users/Admin/Documents/网球/src/rallymate_service/training_evaluation.py:72)、[挥拍阶段](C:/Users/Admin/Documents/网球/src/rallymate_scoring/temporal_recognition.py:276)、[挥拍测量](C:/Users/Admin/Documents/网球/src/rallymate_scoring/stroke_analysis.py:103)。

## 可测量、证据合格与技术评分分别判断

质量要求分层生效：点级默认置信度至少 0.25，坐标必须有效；当前通用特征层通常要求片段有效比例至少 0.50，且特征值实际存在。指标卡中的 70% 是另一层来源要求，不能替代或概括所有实现门限。主球员覆盖不足可使测量不可用；身份、阶段边界、关键点跳变、左右交换或独立目标方向未核验时，部分测量仍可保留用于复核，但正式评分被阻断。“有数值”不等于“可出技术等级”。

来源：[点级有效性](C:/Users/Admin/Documents/网球/src/rallymate_features/validity.py:6)、[通用特征门限](C:/Users/Admin/Documents/网球/src/rallymate_features/event_features.py:45)、[分层质量策略](C:/Users/Admin/Documents/网球/src/rallymate_scoring/quality_policy.py:21)。

平滑和断段也按模块处理。通用 `smooth_series` 默认只插值端点间隔不超过 160 ms 的短缺口，再做半径 100 ms 的时间加权平滑，部分调用会调整这些参数。挥拍中的肩髋二维测量另有连续性检查：连续样本少于 7 个、时长少于 200 ms，或对应整段/阶段覆盖不足 80%，不输出相应范围的摘要；符合条件的局部窗口可以单独保留。该肩髋模块不会跨缺测段插值，也不能据此声称所有基础速度函数都采用同样的断段规则。

参见 [通用信号平滑](C:/Users/Admin/Documents/网球/src/rallymate_features/smoothing.py:80) 与 [肩髋连续性条件](C:/Users/Admin/Documents/网球/src/rallymate_scoring/rotation_analysis.py:17)。

### 当前 0～100 分怎样产生

这是测量证据参考分：可测片段比例占 30%，必需特征覆盖占 20%，特征置信度中位数占 15%，跨独立片段重复性占 25%，评分证据通过比例占 10%。各分量按百分数加权。重复性对普通数值使用四分位距相对尺度衡量，对方向用圆统计、对侧别代码用众数比例，最后取可用特征重复性的中位数；重复动作一致不代表动作正确。

缺失置信度或重复性不补 0 分、50 分或满分，而是移除该分量并将其余权重归一化。但候选片段中的缺测仍会降低可测比例和覆盖率。没有可用测量则不出分。动作分和总分取可用指标参考分的无权平均，再取整，同时展示覆盖指标数。

**虚构计算例 2，仅解释加权：** 五项分量为 80、70、90、83、60 时，参考分=80×0.30+70×0.20+90×0.15+83×0.25+60×0.10=78.25，展示 78。若重复性缺失，则该项退出，其余权重除以 0.75；结果为 57.5/0.75≈76.67，展示 77。这不能读作“网球技术 77 分”。

正式 A～E 另有教练标定链：以教练真值拟合单特征四切点规则或多特征有序回归，通过独立测试和发布条件，并绑定姿态、特征、事件等版本后使用。缺少合格标定时 grade 为空，状态为 calibration_required；当前参考分模块的技术分始终为空。因此本手册不提供未经标定的“膝角超过多少扣几分”。

来源：[参考分权重及缺测](C:/Users/Admin/Documents/网球/src/rallymate_service/training_evaluation.py:18)、[重复性](C:/Users/Admin/Documents/网球/src/rallymate_service/training_evaluation.py:360)、[分量汇总](C:/Users/Admin/Documents/网球/src/rallymate_service/training_evaluation.py:511)、[总分聚合](C:/Users/Admin/Documents/网球/src/rallymate_service/training_evaluation.py:657)、[正式标定门禁](C:/Users/Admin/Documents/网球/src/rallymate_scoring/scoring.py:271)、[正式评分后端](C:/Users/Admin/Documents/网球/src/rallymate_scoring/scoring.py:481)。

## 时序模型的输入怎样组织

当前挥拍分析默认使用 `RuleTemporalBackend`。代码已经定义可替换的时序后端接口，但不能把接口存在解释为已接入网球专项时序神经网络。

时序窗口采用 `M,T,V,C` 布局：`M` 是人数，当前窗口为 1；`T` 是时间步数；`V` 是 Halpe26 的 26 个关节；`C` 是二维坐标的两个分量。与此同时保留 `keypoint_score`、`observed_mask`、`confidence_available_mask`、原视频时间戳和源帧号。

这里的坐标单位声明为 `torso_length`，属于身体相对的图像坐标，x 向右、y 向下；它与原图像素坐标、按图像长边归一化的坐标、步伐模块的综合身体尺度都要区分。布局相同也不能直接当成某个 COCO/PoseC3D 预训练模型已经适配的输入。

默认固定网格为 20 Hz，按最近真实观测匹配，容差为半个采样间隔；没有观测的格子用掩码标记。**20 Hz 是后续时序输入的采样网格，不是视觉模型处理速度，也不是原视频帧率。** 缺测位置存储的零值不能作为真实坐标进入计算。

几何测量仍保留原始不等间隔观测。窗口还要维持同一人物、同一主球员选择段、同一相机参考段和同一归一化依据，不能把身份或镜头变化两侧拼成一个连续动作。

参见 [时序窗口结构与生成](C:/Users/Admin/Documents/网球/src/rallymate_scoring/temporal_recognition.py:52)、[数据布局及掩码](C:/Users/Admin/Documents/网球/src/rallymate_scoring/temporal_recognition.py:124) 和 [当前默认时序后端](C:/Users/Admin/Documents/网球/src/rallymate_scoring/stroke_analysis.py:204)。

## 输出怎样回到原视频和页面

测量结果通过视频、人物、事件和时间段关联回放。分数只是报告中的一部分；有效测量值、单位、缺测原因和时间位置都需要保留。

| 产物或接口 | 作用 | 阅读时应注意 |
| --- | --- | --- |
| `frames.jsonl` | 逐帧检测、姿态和源时间记录 | 原始观测是后续计算的基础 |
| `primary-player.jsonl` | 主球员选择时间线 | 人物选择改变时需要分段 |
| `events.jsonl` | 步伐候选及阶段时间信息 | 候选不是已经确认的触地或触球 |
| `features.jsonl` / `indicator-features.jsonl` | 特征值、指标组合和证据 | 缺测、质量条件和单位与数值一起读取 |
| `scores.jsonl` | 正式指标评分状态 | 未标定时可有测量而无正式等级 |
| `GET /v1/jobs/{job_id}/pose-preview` | 骨架回放窗口 | 便于对照点是否落在正确身体部位 |
| `GET /v1/jobs/{job_id}/demo-result` | 面向网页的综合结果 | 区分参考分、实际测量与技术等级 |

主要写出位置见 [评分闭环产物](C:/Users/Admin/Documents/网球/src/rallymate_scoring/loop.py:602)。完整请求参数与返回字段仍以 [接口手册](RallyMate_API接口详解_2026-10-09.html) 为阅读入口。

## 与 MiniMax 讨论替代方案时怎样划定边界

如果对方提供另一种视觉模型，可以按以下三种接入层分别验证，避免把“能描述动作”直接当作所有测量链路已被替代。

| 接入层 | 需要交付什么 | 用什么判断是否可用 |
| --- | --- | --- |
| 检测或姿态层 | 人、球、球拍位置；关节名称与顺序；原图坐标、逐点分值、时间和身份 | 人工标注下的定位误差、漏检、左右点交换、遮挡恢复、跨帧抖动 |
| 动作理解层 | 动作类型、候选时段、阶段起止及不确定原因 | 对照人工动作与阶段标注；误报、漏报、时间偏差及不可判断比例 |
| 技术评价层 | 绑定具体片段的判断和依据 | 独立教练评价的一致性；证据是否支持结论；重复分析是否稳定 |

如果替换姿态层，优先保持现有的原图坐标、关节语义、人物身份与时间契约。只有 COCO17 输出的模型，不能凭空补出 Halpe26 脚跟和脚趾；需要显式回退相应特征。新模型的分值分布可能不同，原有质量阈值需要重新验证。

如果替换动作理解层，可以保留已有坐标测量，让新模型负责类型、阶段和证据解释。若希望直接输出技术结论，则要单独与教练标签对照。模型改变后，原标定资产是否仍适用也需要检查，不能只替换权重文件就宣称评分等价。

可以直接问对方：

> 你们能否在我们的同一段原视频上，输出同一个人的连续关键点、置信度、源时间，以及动作阶段？我们希望保留回放依据，分别验证点的位置、下游角度与速度的误差，再验证技术结论。对于看不清或证据不足的部分，也需要明确返回无法判断。

## 常见误解与准确说法

| 容易混淆的说法 | 当前实现应怎样表述 |
| --- | --- |
| 26 个关键点就是 26 个评分指标 | 26 是关节输出数量；一个指标通常组合多个关节和多帧观测 |
| YOLO 或 RTMPose 直接给技术分 | 它们提供检测与姿态；测量和评分在后续模块完成 |
| 肩髋夹角就是实际三维转体角 | 当前为图像平面投影测量，受机位与遮挡影响 |
| 髋中心速度就是重心速度或实际跑速 | 当前是几何代理点的画面运动，并以身体尺度等单位表示 |
| 手腕速度峰值就是触球时刻 | 它是运动时序锚点，当前挥拍输出仍声明触球未确认 |
| 关键点分值高就代表技术好 | 分值用于观测筛选；技术好坏需要独立标准和教练标定 |
| 参考分越高就一定比另一个人打得好 | 当前参考分描述证据，不用于跨视频比较技术水平 |
| realtime 配置表示全系统保证实时 | 端到端延迟需要在具体视频、设备、并发条件下测量 |

## 源码阅读顺序与核对范围

建议先读 `pipeline.py` 看流程，再读 `inference.py`、`pose/adapters.py` 和 `pose/rtmpose_backend.py` 看模型输入输出；随后阅读 `coordinates.py` 与特征模块，最后查看动作规则、`training_evaluation.py` 和 `scoring.py`。

本手册核对了源码引用与教学算例，没有据此新增模型精度、实时性或技术评分准确率结论。真实准确率需要独立标注的视频测试；这次文档编写没有启动模型推理或修改模型及评分逻辑。

| 文件 | 快照 SHA256 前 12 位 |
| --- | --- |
| `models/rtmpose/deployment-presets.json` | `a95b7b5f225c` |
| `MMPose 依赖 / rtmpose-m_8xb512-700e_body8-halpe26-256x192.py` | `606a725342f6` |
| `MMPose 依赖 / inference.py` | `37e7c59a5243` |
| `MMPose 依赖 / simcc_label.py` | `343da9376514` |
| `MMPose 依赖 / post_processing.py` | `51d121bcb5a3` |
| `MMPose 依赖 / common_transforms.py` | `a1596938bdee` |
| `MMPose 依赖 / rtmcc_head.py` | `daf3c300f6a0` |
| `MMPose 依赖 / topdown.py` | `f954aab7f7dd` |
| `src/rallymate_events/rules.py` | `c365775116d6` |
| `src/rallymate_features/coordinates.py` | `ead4df0461f4` |
| `src/rallymate_features/event_features.py` | `811f985e7646` |
| `src/rallymate_features/fs01_fs02_features.py` | `39e34739fd1e` |
| `src/rallymate_features/geometry.py` | `6c9c9ce323fd` |
| `src/rallymate_features/kinematics.py` | `8a0c7dbc563e` |
| `src/rallymate_features/smoothing.py` | `7eb23f9de186` |
| `src/rallymate_features/validity.py` | `baee5cccdb51` |
| `src/rallymate_scoring/data/metric_cards.json` | `43180db292ff` |
| `src/rallymate_scoring/feasibility.py` | `0613ceb80408` |
| `src/rallymate_scoring/loop.py` | `22b79515340b` |
| `src/rallymate_scoring/quality_policy.py` | `3e07d53d3aff` |
| `src/rallymate_scoring/rotation_analysis.py` | `20d18e41b47b` |
| `src/rallymate_scoring/scoring.py` | `0c574dc61385` |
| `src/rallymate_scoring/stroke_analysis.py` | `298f6674d650` |
| `src/rallymate_scoring/temporal_recognition.py` | `e67d5bf0fbd0` |
| `src/rallymate_service/api.py` | `15cfb21fe928` |
| `src/rallymate_service/config.py` | `177bcd48cc02` |
| `src/rallymate_service/training_evaluation.py` | `ba29ead2936b` |
| `src/rallymate_tracking/primary_player.py` | `cb1a8792c8eb` |
| `src/rallymate_vision/inference.py` | `3a339927aa9f` |
| `src/rallymate_vision/pipeline.py` | `be8b581e3a8a` |
| `src/rallymate_vision/pose/adapters.py` | `630c5be85a30` |
| `src/rallymate_vision/pose/data/keypoint_schemas.json` | `3c747b9b0685` |
| `src/rallymate_vision/pose/rtmpose_backend.py` | `f662f2b582d7` |
| `src/rallymate_vision/timebase.py` | `eb14646e2e80` |
| `src/rallymate_vision/tracking.py` | `d909f4971304` |
| `src/rallymate_vision/utils.py` | `34c9ab904bd0` |
