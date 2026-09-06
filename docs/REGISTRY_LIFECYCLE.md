# Registry Lifecycle Authority

> 当前 authority：`registry-lifecycle-2026-08-30.1`  
> 机器清单：`registry-lifecycle.json`  
> Schema：`contracts/registry-lifecycle.schema.json`

## 目的

成熟度 registry 的“文件存在且 Schema 合法”不足以证明它可用于当前生产运行。Lifecycle
authority 用固定角色、原始文件 SHA-256、内嵌版本和来源版本，把“当前”“派生当前”“历史”
与“仅规划”明确分开；不提供 `latest`、目录扫描或自动回退。

当前固定角色：

- `runtime_feasibility`：唯一生产成熟度 registry，当前为
  `metric-feasibility-pose-wave-v2.json` / `pose-wave-2026-08-22.17`；
- `current_scoring_requirements`：由该 registry 派生的当前 13 项评分依赖快照。

明确不可作为运行角色的根工件：

- `metric-feasibility.json`：`historical`，只允许显式历史回放；
- `metric-measurement-plans.json`：`planning_only`，仍绑定旧 `.8` feasibility，不能覆盖
  当前 `.17` registry 或评分依赖快照。

## 生产门禁

`rallymate-vision`、API/worker、`score_calibrated_indicators.py` 和生产模式的
`promote_calibration_candidate.py` 都从运维侧 manifest 解析 `runtime_feasibility`。请求或
`--feasibility-registry` 只能断言解析后的精确路径，不能选择另一个 registry；历史文件、未登记
文件和放在其他路径的同字节副本均 fail closed。

解析器对 manifest 和目标工件各执行一次 `read_bytes()`，并在同一份 bytes 上完成 raw SHA、
UTF-8/JSON、语义与版本校验。下游直接消费已验证 payload；正式管线在推理前、评分后和最终
汇总前复核 manifest/工件 raw SHA。Job request 不含也不接受 manifest 字段。

服务部署使用：

- `RALLYMATE_SCORING_REGISTRY_LIFECYCLE_MANIFEST`：运维只读 manifest；
- `RALLYMATE_SCORING_FEASIBILITY_REGISTRY`：可选精确路径 pin，不是选择器。

两者以及 manifest 实际解析出的当前 registry 都不能位于 job 可写的 `uploads`、`requests`
或 `runs` 目录。Docker 镜像只把 `service_data` 与模型目录交给服务账号写入；manifest、当前
registry 和 requirements 快照保持 root-owned。普通 wheel 尚未内置这些根目录工件，因此未
挂载 authority 的 wheel 部署会 fail closed，不是可用的独立发布包。

## 历史回放

四个仍复现旧六指标基线的脚本必须显式传 `--historical-replay`。它们固定旧版本、精确六个
指标 ID 和原始文件 SHA；Pose A/B 使用同一份已校验 bytes 快照，不在校验后重开 registry。
报告与 `--reuse-complete` 还会核对视频/事件身份、每事件精确指标集合、record/score 对应、
registry 版本及 grade/threshold 为空；当前 `.17` 产物不能重新贴成历史标签。缺旗标、换成
当前 13 项文件或修改历史文件都会失败，逐运行产物和聚合输出都必须带
`historical_replay` 标识。低层 `load_feasibility_registry` 和
`run_minimum_scoring_loop` 继续支持测试/回放，不构成生产入口。

## 发布更新

更新当前角色时应一次提交：目标工件、manifest 中的 relative path/raw SHA/embedded version/
source version、相关 Schema/测试和部署镜像。不得只改文件名或手工复制一个“最新”文件。

验证命令：

```powershell
$env:PYTHONPATH = "src"
python -m unittest -v tests.test_registry_lifecycle `
  tests.test_registry_lifecycle_integration `
  tests.test_pipeline_scoring_registry `
  tests.test_calibrated_scoring_cli `
  tests.test_historical_registry_scripts
```

该门禁只证明当前运行使用了被登记的 registry 身份；它不会把 F2 提升为 F3/F4，也不会创造
教练真值、准确率或 A～E 授权。
