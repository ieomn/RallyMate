# RallyMate

RallyMate 是面向网球训练复盘的本地视频分析原型。系统对上传视频执行画面质量检查、
目标检测、人员跟踪、人体姿态估计、候选动作分段和指标测量，并生成结构化结果与自然语言训练建议。

> 当前用户侧分数是“动作表现参考分（Beta）”，仅用于训练复盘。它不是事件检测准确率、
> 关键点真实误差、教练正式评分或 A～E 等级。

## 当前能力边界

当前版本已经实现：

- 本地网页上传，以及单个或多个视频按顺序分析；
- `player / ball / racket` 检测、同类框去重与人员跨帧跟踪；
- RTMPose-M Halpe26 26 点人体姿态估计，YOLO Pose 作为显式回滚基线；
- 可见场地区域识别，以及可选的人工四点场地标定；
- FS01、FS02、FS09 候选事件、关键阶段与证据帧；
- 13 项 Pose-only 指标的 Beta 分、自然语言观察、证据摘要和训练建议；
- JSONL、汇总 JSON、预览图和可选骨架标注视频；
- GPU/CPU、模型档位和运行配置的结果留痕。

当前 13 项指标包括：

- FS01“准备与分腿垫步”：4 项；
- FS02“第一步启动”：4 项；
- FS09“制动与重新稳定”：5 项。

当前版本尚不能声明：

- 事件检测准确率，因为尚缺完整、独立的人工事件真值；
- 踝、膝、髋等关键点真实误差，因为尚缺充分的人工校正关键点；
- 正式 0～100 分或 A～E 等级，因为尚缺多教练真值、标定资产和独立测试；
- Ball、Racket、Court 相关技术评分，或完整 298 项指标均已进入评分闭环。

298 项是长期指标注册范围，不是当前已实现评分数量。当前 13 项成熟度仍为 F2：
系统能够输出可审计的二维运动学特征，但尚未完成正式评分所需的真值、误差评测和可信标定。

## 处理链路

```text
视频上传 -> 格式与画面质量检查 -> 球员 / 球 / 球拍检测
  -> 人员跟踪与主球员选择 -> 人员 ROI 内运行 Halpe26 Pose
  -> FS01 / FS02 / FS09 候选事件 -> 13 项指标特征与证据质量
  -> Beta 训练反馈与自然语言建议
```

人体 Pose 是按人员框级联执行的。每组姿态通过 `person_track_id` 关联到对应的球员轨迹，
避免把不同球员的关键点和动作证据混合。

## 环境要求

当前主要验证环境为 Windows、PowerShell 和 Python 3.10。
CPU 可以运行，但完整视频逐帧分析速度较慢；NVIDIA GPU 为推荐配置，需要安装与本机驱动兼容的
CUDA 版 PyTorch。首次准备环境和模型需要网络连接，并应为模型、样例和结果预留充足磁盘空间。

## 从全新克隆开始

以下命令不依赖固定用户名或本机绝对路径：

```powershell
git clone https://github.com/ieomn/RallyMate.git
Set-Location RallyMate

py -3.10 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e ".[service,dev]"

$BasePython = (Resolve-Path .\.venv\Scripts\python.exe).Path
.\scripts\prepare_rtmpose_runtime.ps1 -BasePython $BasePython

.\.venv\Scripts\python.exe .\scripts\download_assets.py --root .
```

资源准备脚本会下载演示视频、YOLO 检测权重和当前默认 RTMPose-M Halpe26 权重。
RTMPose 权重会按登记的文件大小和 SHA-256 校验。

启动本地网页、API 和单 Worker：

```powershell
.\scripts\run_local_inference.ps1 `
  -PosePreset rtmpose-m-halpe26-online `
  -Device auto
```

启动后访问：

- 用户上传页面：`http://127.0.0.1:8000/`
- OpenAPI：`http://127.0.0.1:8000/docs`
- 存活检查：`http://127.0.0.1:8000/health/live`
- 就绪检查：`http://127.0.0.1:8000/health/ready`

无可用 CUDA 时可显式使用 CPU：

```powershell
.\scripts\run_local_inference.ps1 -Device cpu
```

快速命令行烟测：

```powershell
.\scripts\run_demo.ps1 -Request .\examples\request-quick.json
```

## API 最小示例

提交视频：

```powershell
curl.exe -X POST "http://127.0.0.1:8000/v1/jobs" `
  -F "video=@data\samples\pexels-tennis-match-992693.mp4" `
  -F "court_mode=auto" `
  -F "write_annotated_video=false"
```

响应中的 `id` 是任务 UUID。使用以下接口读取状态和用户结果：

```text
GET /v1/jobs/{id}
GET /v1/jobs/{id}/demo-result
```

API 为兼容旧客户端仍将 `write_annotated_video` 的表单默认值保留为 `true`。
不需要骨架视频时应明确传入 `false`，以减少 CPU 编码和磁盘占用。

## 结果语义

- `training_evaluation.score_0_to_100` 是当前 13 项的 Beta 训练参考分；
- Beta 单项综合可测实例比例、特征覆盖、Pose 置信信息、跨片段重复性和证据完整度；
- Beta 总分是已形成评价的白名单指标分数算术平均，不是正式跨指标评分公式；
- `formal_scoring.available=false` 表示正式评分尚未启用；
- `formal_scoring.score_0_to_100=null` 和 `formal_scoring.grade=null` 不得替换成 0；
- `status="unavailable"` 表示证据或输入不足，不表示动作得 0 分；
- `status="calibration_required"` 表示仍缺正式标定或发布授权，不是技术等级；
- `final_demo_score`、`analysis_quality` 和 `display_score` 是兼容字段，不是当前主展示分。

主要运行产物包括逐帧观测 `frames.jsonl`、候选事件 `events.jsonl`、指标测量
`indicator-features.jsonl`、状态合同 `scores.jsonl`、汇总 `summary.json`、预览图和可选标注视频。

## 仓库结构

```text
src/
  rallymate_vision/          视频、检测、Pose、场地与运行产物
  rallymate_tracking/        人员轨迹与身份连续性
  rallymate_events/          FS 候选事件与阶段
  rallymate_features/        事件级运动学特征
  rallymate_scoring/         registry、成熟度与评分门禁
  rallymate_service/         API、任务数据库、Worker 和用户页面
  rallymate_annotation/      人工真值工作台与接收流程
  rallymate_evaluation/      事件、关键点和特征评测
  rallymate_training/        数据准备、训练、导出与模型登记
contracts/                   JSON Schema 与跨模块数据合同
models/rtmpose/              候选和部署预设元数据，不含权重
examples/ · scripts/         示例、配置及运行维护脚本
tests/ · docs/               测试与当前说明书
reports/                     经筛选的小型合同与审计摘要
runtime/rtmpose/             隔离运行时说明与依赖锁定，不含本地虚拟环境
scoring-demo-web/            298 项指标可追溯展示前端
```

`service_data/`、`runs/`、模型权重、原始视频和大体积实验报告属于本地或生成内容，
不应提交到代码仓库。

## 测试范围

以下测试不需要下载视频或加载模型，适合在全新克隆中验证用户结果合同：

```powershell
.\.venv\Scripts\python.exe -m unittest `
  tests.test_training_evaluation `
  tests.test_user_demo `
  tests.test_worker_cpu_threads `
  -v
```

完整测试目录还包含与历史实验、人工标注包、密封测试集或本地生成产物绑定的证据测试。
这些测试不等同于可在公开全新克隆中执行的自包含单元测试。

端到端推理会下载外部资源并实际加载模型，耗时和显存占用取决于视频、设备和模型档位。
执行前应确认资源来源、磁盘空间和运行环境。

## 隐私、保留与部署安全

本地服务会把原始上传视频、内部请求、SQLite 任务记录和派生结果写入 `service_data/`。
当前版本没有自动过期清理策略，也没有面向用户的删除任务接口；停止服务不会自动删除这些文件。

处理真人视频前，应由部署者明确取得授权，并制定访问控制、用途范围、保留周期、删除流程和备份策略。
不要把 `service_data/`、人工标注源视频、私有标注结果或含个人信息的日志提交到 Git。

本地开发入口默认绑定 loopback。若部署到其他机器或网络，至少需要配置强随机 API key、
TLS 反向代理、网络隔离、上传限额、速率限制和访问审计。当前单 Bearer key 不是完整的用户或租户权限系统。

浏览器批处理恢复状态只保存不透明任务 ID，不保存 token、视频字节、文件名或结果正文；
这不改变服务器端原视频和结果会持续落盘的事实。

## 模型、样例和商业使用

仓库不包含第三方模型权重。`scripts/download_assets.py` 会按部署预设下载所需资产：

- YOLO 检测与回滚 Pose 权重由 Ultralytics 获取；
- RTMPose Halpe26 权重来自 OpenMMLab 模型资源；
- 演示视频来自 Pexels，并仅用于可复现烟测。

使用者必须分别核对代码依赖、模型权重、演示素材和自有视频的许可证及授权范围。
开发环境能够运行，不代表已经满足闭源商业发布、数据再分发或训练使用条件。
本节只说明第三方资产边界，不替代项目所有者对仓库代码许可方式的决定。

## 核心文档

- [产品说明书（当前态）](docs/RallyMate产品说明书_当前态_v1.0.md)
- [技术架构与实现说明书（当前态）](docs/RallyMate技术架构与实现说明书_当前态_v1.0.md)
- [接口与端到端链路（当前态）](docs/RallyMate接口与端到端链路_当前态_v1.0.md)

## 保留的审计记录

- [Demo v1.2 摘要](reports/user-demo-v1.2/summary.md)、[合同](reports/user-demo-v1.2/demo-contract.json)与[字段记录](reports/user-demo-v1.2/field-change-record.json)
- [Demo v1.1 历史摘要](reports/m96-user-demo-v1.1/summary.md)、[合同](reports/m96-user-demo-v1.1/demo-contract.json)与[字段记录](reports/m96-user-demo-v1.1/field-change-record.json)
- [M96 关键点评测试点摘要](reports/m96-pose-pilot/summary.md)、[合同](reports/m96-pose-pilot/pilot-contract.json)与[字段记录](reports/m96-pose-pilot/field-change-record.json)
- [M96 同帧诊断摘要](reports/m96-rtmpose-same-frame-diagnostic/summary.md)与[字段记录](reports/m96-rtmpose-same-frame-diagnostic/field-change-record.json)
- [M97 测量恢复摘要](reports/measurement-recovery-m97/summary/summary.md)与[字段记录](reports/measurement-recovery-m97/field-change-record.json)

这些记录用于追溯字段、运行合同和候选模型决策，不构成正式准确率、教练评分或生产发布证明。
