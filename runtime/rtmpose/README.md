# RallyMate RTMPose 隔离运行时

当前 Windows 验证运行时复用现有 CUDA PyTorch/Ultralytics 系统包，但把 MMPose 依赖和 NumPy 1.x ABI 放在独立 venv：

```powershell
.\scripts\prepare_rtmpose_runtime.ps1
$env:PYTHONPATH = "$PWD\src"
.\runtime\rtmpose\.venv\Scripts\python.exe .\scripts\benchmark_rtmpose_candidates.py --root .
```

固定版本见 `requirements-lock.txt`。生产 yolo 环境仍为 NumPy 2.2.6；RTMPose venv 内固定 NumPy 1.26.4，以兼容 `xtcocotools` 的二进制 ABI。

本运行时使用 `mmcv-lite`。`RtmposePoseBackend` 在导入前屏蔽仅 EDPose 需要的可选 transformer head，并把 RTMPose backbone 明确解析到 MMPose 自带 CSPNeXt；模型实际加载已用三份官方 Halpe26 checkpoint 验证。运行时可能打印缺少 `MultiScaleDeformableAttention` 的提示，但 RTMPose/RTMCCHead 不调用该算子。

PyTorch 2.6+ 默认 `torch.load(weights_only=True)`，旧版 MMEngine checkpoint loader 未显式传参。Backend 只在加载已登记 SHA-256 的官方 checkpoint 时临时恢复历史 `weights_only=False` 行为，加载后立即还原全局函数。不要对未登记来源的 checkpoint 使用该 Backend。

当前 P3 runtime 为 `pytorch` 验证后端，并非已晋级的 Windows 生产后端。ONNX Runtime/TensorRT 导出与数值一致性通过前，不得把候选设为默认。

## 部署预设与启动

预设注册表为 `models/rtmpose/deployment-presets.json`：

- `rtmpose-m-halpe26-online`：RTMPose-M 256×192，`realtime`，在线主候选；
- `rtmpose-m-halpe26-analysis`：RTMPose-M 384×288，`analysis`（含 flip-test），离线深度分析候选；
- `yolo-baseline`：COCO17 粗粒度基线，仅作为显式回退预设。

普通视频上传、本地 API 和 Worker 当前默认使用 `rtmpose-m-halpe26-online`。只有在排障或兼容性验证时显式指定 `yolo-baseline`，才会回退到 YOLO Pose；该回退只替换姿态测量后端，不会启用正式 A～E 等级或绕过评分标定门禁。

运行两档 Persistent Worker/Pipeline 烟测：

```powershell
.\scripts\run_pose_deployment_smoke.ps1 -Profile Online
.\scripts\run_pose_deployment_smoke.ps1 -Profile Analysis
```

启动本地 API + 常驻 Worker：

```powershell
.\scripts\run_rtmpose_service.ps1 -Profile Online -Port 8000
```

API 与独立 Worker 分开部署时：

```powershell
.\scripts\run_rtmpose_worker.ps1 -Profile Online
```

这些脚本只切换 Pose 后端，人体检测仍由 YOLO 完成。预设会把 checkpoint、MMPose config、profile 和 Halpe26 拓扑作为一组绑定，避免环境变量组合漂移。详情与本机实测见 `docs/POSE_DEPLOYMENT_PROFILES.md`。
