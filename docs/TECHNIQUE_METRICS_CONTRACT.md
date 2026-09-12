# RallyMate 技术指标与证据契约

## 目的

`src/rallymate_scoring/data/technique_metrics.json` 是 2026-09 版动作定义注册表。它把用户提供的五份 Word 规则文档整理成可版本化、可测试、可由前端读取的动作目录，同时保留原有 298 张 GS/FS 指标卡的兼容性。

注册表记录动作阶段、视觉特征、必需证据、增强证据和代理限制。每个来源条目还保留了
导入时的 DOCX SHA-256，便于审计人员核对“定义来源”而不是把附件中的说明误当成运行指令。
五份文档没有提供阶段权重、总分公式或 A—E 阈值，因此注册表不会自行补写数值评分政策。

## 证据边界

- 人体姿态和连续跟踪可以支持重心、关节、肩髋、脚步和头部朝向代理。
- 球轨迹是判断高球、抛球、来球响应、击球窗口和回位方向的增强证据；通用 sports-ball 框中心不能直接当作专项球路或旋转模型。预览最多返回 8 个点，但 `predicted_covered_horizon_ms` 会记录是否覆盖请求的短时窗口。
- 当前球拍模型只输出通用检测框和 track 置信度。没有拍头、拍柄、拍面、甜区关键点时，只返回 `bbox_only` 和候选击球窗口线索。
- 场地标定与对手/球路信息不足时，只报告是否发生位移或回位，不判断战术位置是否最优。
- 缺失证据不会被补成零分或虚构事件；技术评估返回 `not_observed`、`unavailable`、`partial` 或 `ready`，并且 `score_0_to_100` 保持 `null`，直到教练标定服务接入。
- `coverage_detail` 会记录每类证据的 fraction、字段和来源；`pose`/`tracking` 优先使用 `primary_player` 诊断，旧摘要才回退到全局 `coverage`，并明确标记 `fallback_used`。
- 触球状态遵循版本化 contact-window policy：没有 `strike` 阶段（包括步伐）为 `not_applicable`；球与球拍覆盖均大于 0 只表示 `contact_window_proxy`，只有一侧表示 `impact_window_only`，两侧都没有或未观测为 `unavailable`。这不是触球准确率或物理模型。
- `evidence_score_0_to_100` 只是服务端证据就绪度信号：当前实现用必需证据 70% + 增强证据 30%，并以 0.4/0.72 作为 `partial`/`ready` 操作门槛；这些数值写在返回的 `policy.readiness` 中，明确标为服务运行门禁，不是五份 Word 定义提供的教练评分权重或阈值。
- 球拍轨迹当前 `association_status=unassociated`，因为 Stage 1 尚未把通用球拍框与主球员时间线绑定；不能将其展示为目标球员球拍。

## 服务接口

当前新增接口以 `/v1/meta` 的 `api_version=1.3.0` 标识；OpenAPI `info.version` 暂保留
`1.2.0`，作为旧客户端兼容标签。联调时应读取 `/v1/meta`，不要仅依赖 OpenAPI 展示版本。

- `GET /v1/techniques`：动作目录、阶段契约、来源文档和版本哈希。
- `GET /v1/jobs/{job_id}/technique-assessment`：针对一次已完成任务的证据就绪度。
- `GET /v1/jobs/{job_id}/trajectory`：球观测点、短时常速度可视化预览和球拍 bbox 轨迹。
- `GET /v1/meta`：前端部署发现文档，包含上传、任务、结果和轨迹接口模板。

所有新增结果都带有版本字段和明确的非正式语义，适合前端展示和 AutoDL 推理节点之间的稳定传输；它们不改变旧版正式标定接口。
对应的机器合同分别位于 `contracts/technique-assessment.schema.json` 和
`contracts/trajectory-preview.schema.json`，测试会对代表性响应重放 Draft 2020-12 校验。

## AutoDL 与域名部署

API 容器通过以下环境变量与前端解耦：

```text
RALLYMATE_PUBLIC_BASE_URL=https://app.example.com  # 同源网关；独立 API 模式可用 api.example.com
RALLYMATE_CORS_ORIGINS=https://app.example.com,http://localhost:3000
RALLYMATE_TECHNIQUE_REGISTRY=/app/technique-metrics-audited.json  # 可选只读覆盖
```

前端只应使用 `GET /v1/meta` 或构建时的 API base URL，不要把 worker 的文件系统路径写入浏览器。生产环境仍需配置 bearer token、模型许可证和只读标定资产；`service_data` 仅用于上传、请求和运行产物。
