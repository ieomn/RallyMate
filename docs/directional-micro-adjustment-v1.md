# FS05 图像方向微调应用规则 v1

本模块为 FS05-M03（前后）和 FS05-M04（左右）增加独立可调用的应用数值入口。原目录和原文 A–E 保持原样；`original_grade=null`、`source_grade_supported=false`。它尚未接入统一 298 卡报告、HTTP 路由或前端。不能将这两个适配器计作完整原文技术等级已实现。

源定义分别保存在 `card_rules_v1.json` 的 CARD-FS T024/T025。M03 的 P0854 定义为前后小步修正与预计击球点的距离，P0856 明确涉及双踝，P0862 允许无标定时图像二维代理，P0874 明确无法据缺失球轨迹判断距离是否合理。M04 对应横向调整。原文没有“只有一个脚能动”要求，因此观察脚完整周期并不要求另一脚静止、路径更小或没有第二个周期。

## 输入与计算

`build_directional_micro_adjustment_report(run_dir, document, video_path=...)` 校验目录、summary、每条帧记录的同一 run，四个完整 artifact SHA、原视频 SHA，并重新解码原视频 PTS。计算中不读取调用方的 pass/grade。`compute_directional_micro_adjustment(records, primary, events, decoder_timeline, document, source_job_id=...)` 是原记录重放入口，始终不声称已核验原媒体文件。

文档版本为 `directional-micro-adjustment-annotations-v1.0.0`。窗口沿用已有 FS 原帧闭区间和人工来源字段，另需 `step_motion_observation` 及 `direction_reference`。后者版本为 `manual-image-direction-reference-v1.0.0`，保存同视频、同人、primary/derived/camera epoch、原帧号和 PTS、原图中的两点直线、像素不确定性、来源说明。纵/横轴必须各自观察；透视图中不能把一个轴旋转 90° 当成另一个球场轴。可见球中心只表示该原帧中可见目标，不能称预测击球点、意图或合理击球距离。轴或目标缺失时不评分。

六个共同关节为双肩、双髋、双踝，分母是窗口内全部解码原帧；必须逐帧都有合法观测，保留原模型来源、双 epoch 和原时钟。既有击球流程可能把长轨迹统一成单侧躯干坐标。本消费者先逐帧核对旧归一化坐标、旧 center/scale/basis 与原帧重解析的 reference 点，再在复制的局部样本中重建 full_torso，保留 `local_normalization` 的输入和输出证明。缺失点不会由另一侧或邻帧补齐；共享旧流程不变。

图像线和目标使用该原帧的有效相机变换，转换到与姿态相同的补偿参考平面一次。固定尺度为整窗肩中心至髋中心距离中位数；端点分别取前后 `max(3,ceil(N*.2))` 帧的坐标中位数。观察脚需有低速→运动→低速三段，每段至少 3 帧及 80 ms，运动速度严格大于 0.5 躯干/秒，端点窗口不得跨运动段，前后低速点云须可区分。这只是完整二维运动，不确认触地、技术步型或场地真实方向。

`image-projected-small-adjustment-v1.0.0` 有三个目的不同、等权的应用条件：

- 沿所观察轴，到固定可见球中心的投影距离减少至少 0.03 躯干。
- 双踝完整二维路径的最大值在 [0.03, 0.5] 躯干；两个脚各自净位移和路径均保留。往返时净位移很小也不能隐藏大路径。
- 髋净位移在观察轴上的绝对投影 / 髋二维净位移至少 0.7；净位移为零时该比例为 0。

这些数值均为未校准应用阈值，既非 Word 阈值，也非文献或教练标准。完整可算但条件失败仍用统一应用完成度映射正式评分，缺测保持空分。输入原质量证据及其覆盖仍检查，明确软提示保留，确认换人、错源、原窗缺帧等仍阻断。

## 实证边界与复算

`tests/fixtures/directional-micro-adjustment-native-e420-v1.json` 是 e4204ec3 native run 原视频 112–136 的固定开发窗口及精确原记录投影，不能改称同视频的 0a3 运行。窗口最早由旧 locator 提出，现完整自动外窗为 79–136；本批不移动已经查看的 112–136。root 在原帧 112（PTS 2240 × 1/600）的 1280×720 图上观察：纵向线 [1108,599]→[801,458]，横向线 [500,606]→[1000,601]，球中心 [554,349]，像素不确定性 5。它们不是球场标定或专家标签。

实际原媒体入口核验 2911 帧，固定窗 25/25 六关节共同有效。两轴接近量分别约 −0.3850/−0.4491 躯干，因此接近条件失败；双踝最大路径约 0.3003，轴对齐约 0.8522/0.9940。每卡两项完成得到 C / 60。这不是预计击球距离合理或 FS05 技术已完成的结论。先前把单脚唯一性误用于微调的未评分开发收据另存于本地，修订没有移动窗口或调分箱。

紧凑 fixture 的原记录重放不冒充再次原视频 IO；完整媒体集成测试在具备原本地文件时另外执行，缺媒体显式跳过。合同测试含合成正确值、双脚都短步、另一脚大往返路径、缺轴/目标/关节、假旧 normalized 坐标、缺边界帧、错误 epoch、错 run 和相机变换。

复算命令：

```powershell
$env:PYTHONPATH='src'
python scripts/replay_directional_micro_adjustment.py --run-directory <e420原运行目录> --video <同SHA原视频> --annotations tests/fixtures/directional-micro-adjustment-e420-annotations-v1.json --output-directory <输出目录>
python -m unittest tests.test_direction_reference_observables -v
```

输出包含完整原 PTS、模型来源、每帧原/新局部归一化、双脚速度与三段证据、每项实值、源文档 hash 和条件版本。`validation-evidence.json` 分别保存实际导入代码 raw SHA 与 UTF-8 LF SHA；两种算法不能混用。
