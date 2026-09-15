# RallyMate · 网球动作分析

上传视频，在本机或自己的 GPU 服务器进行动作识别，再查看练习参考分、分项观察和标注回放。小米 MiMo 只辅助解释已经核验的结果，不能修改分数。

## 当前功能

- 视频上传、排队与推理进度，刷新恢复任务，网络中断后继续读取。
- 动作参考分、逐项测量、球与球拍观察、H.264 标注视频。
- 五类共 24 项最新动作定义：底线、发球、接发、网前、步伐。
- 导入已有任务报告或分析摘要，导出当前报告。
- 分析过程中可提问；完成后从服务端核验视频证据，再生成建议。

当前稳定测量链覆盖分腿垫步、第一步启动、制动相关的 13 项指标。24 项目录不等于 24 项都已具备可靠评分；未观测动作不补分，参考分与证据就绪度分开显示。

## 本机运行

需要 Node.js 22.13+、已配置模型依赖的 Python 环境和本地模型文件。模型权重、私人视频、运行产物和凭据均不进入 Git。

```powershell
# 首次安装 Python 服务依赖；在已配置 PyTorch 的环境中运行
python -m pip install -e ".[service]"

# 终端一：API 与推理 worker
$env:PYTHONPATH="src"
$env:RALLYMATE_POSE_PRESET="yolo-baseline"
python -c "from rallymate_service.cli import dev_main; dev_main()"

# 终端二：网页
cd scoring-demo-web
npm ci
npm run dev
```

默认网页为 `http://localhost:3000`，API 为 `http://127.0.0.1:8000`。网页通过同源代理上传，不需要把 API 密钥放入浏览器。`GET /health/ready` 检查分析服务是否就绪。Windows 的 service 依赖包含 ffmpeg wheel；Linux 镜像安装系统 ffmpeg，生成 H.264 回放。

已有 RTMPose 环境可使用 `scripts/run_rtmpose_service.ps1` 或 `scripts/run_local_inference.ps1` 选择对应模型预设。

## MiMo 配置与防护

参见 [前端说明](scoring-demo-web/README.md)。本机 Worker 的服务端配置放在忽略提交的 `scoring-demo-web/.dev.vars`；线上使用托管平台 Secrets。不要把密钥写入 `NEXT_PUBLIC_*`、`VITE_*`、源码、测试样例或 Git 远程地址。

模型端点固定，输入字段、长度、来源、频率与并发受限。模型只能生成短摘要并选择服务端批准的提示；练习步骤、强度和时长由服务端确定。未知输出、超时或接口错误会回退为基础建议。出现身体不适的输入直接返回暂停练习，不调用模型。

## 部署

[部署指南](deploy/README.md) 提供 Docker、私有 Nginx 网关、HTTPS 和 DNS 验证步骤。GPU 推理服务与网页分别运行；DNS 只负责域名解析。

云端默认采用私人工作台访问控制，不能直接把共享后端密钥代理开放给匿名访客。多人服务需要独立账户、任务归属与持久配额。本仓库没有绑定实际云服务器或域名，真实云端上传和 DNS 连通性必须在配置目标服务器后验证。

## 验证

```powershell
$env:PYTHONPATH="src"
python -m unittest tests.test_service tests.test_service_boundaries tests.test_training_evaluation tests.test_technique_assessment tests.test_user_demo tests.test_annotated_video_codec
cd scoring-demo-web
npm test
npm run typecheck
npm run lint
```

2026-09-15 本机验收：通过网页同源接口上传 97 秒、2,911 帧的网球视频，约 137 秒完成推理；返回 77/100 参考分、13 项测量、轨迹和 H.264 视频。浏览器核验视频 1280×720、97.03 秒，解码无错误。该记录只证明本机链路，并非动作准确率或云端性能承诺。
