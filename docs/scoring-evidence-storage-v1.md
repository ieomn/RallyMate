# 评分证据存储 v1：开发与运维说明

本次变更把重复的逐帧证据移到共享文件，避免每条特征反复保存整段视频的来源账本。计算使用的原始数值、事件窗口、身份与时间绑定不变；存储格式与读取方式发生变化。

## 文件与来源绑定

`features.jsonl`、`indicator-features.jsonl`、`scores.jsonl` 可包含 `scoring-artifact-evidence-v1` 引用；`events.jsonl` 保持原来的内嵌格式。共享文件固定为同目录下的 `scoring-evidence.sqlite3`。

[artifact_evidence.py](../src/rallymate_scoring/artifact_evidence.py) 对以下字段中的较大对象按内容 SHA-256 去重：`source_pose_person_association`、`source_window`、`geometry_mask`、`camera_compensation`、`frame_dimensions`、`source_time_binding`。目前序列化内容达到 4096 字节才进入池；小对象继续内嵌。

引用对象只有一个保留键 `__rallymate_evidence_ref__`，其中记录 `codec`、`pool`、`binding_sha256`、`sha256`。条目哈希绑定实际证据内容；绑定哈希覆盖 codec、`video_id`、`source_id`、`video_sha256`，防止把其他任务或视频的池拼入当前记录。

评分摘要中的 `evidence_storage` 清单包含上述来源字段，以及固定文件名 `path`、整个池文件的 `sha256` 和 `binding_sha256`。`verify_evidence_manifest()` 校验清单、池内来源与实际文件哈希；业务调用还应传入独立已知的任务/视频身份。池文件缺失、引用损坏或绑定不符会报错，不会降级成缺失测量继续评分。

搬迁或备份新产物时，应保留整个输出目录中的 JSONL、池文件和摘要清单。只复制某个 JSONL 不能形成完整的新格式数据集。写入器先生成临时文件再发布；多个文件的发布不是一个文件系统事务，必须以完整清单和最终校验成功为准。

## 读取与校验

不要用 `json.loads(line)` 直接消费可能包含池引用的评分 JSONL。通用读取入口为：

```python
from rallymate_scoring.artifact_evidence import iter_artifact_records

for record in iter_artifact_records(
    path,
    expected_video_id=video_id,
    expected_source_id=source_id,
    cancellation_callback=check_cancelled,
):
    consume(record)
```

返回值与原来的内嵌对象对应。嵌套证据可能被多个记录共享，应按只读对象使用；需要修改时复制所需部分。需要跨文件复用缓存时，可使用上下文管理器 `ArtifactEvidenceReader`。

默认单条序列化记录/池条目上限为 64 MiB，证据缓存也有独立的 64 MiB 预算；这些预算不等于整个 Python 进程的内存上限。旧的无引用 JSONL 仍可直接通过同一读取入口处理，无需迁移或重写旧哈希。

[validation.py](../src/rallymate_vision/validation.py) 逐条校验评分流，使用临时 SQLite 保存跨记录关联，保留原有的来源、数值、重复键和文件哈希检查；不再把完整特征列表与全视频账本同时留在内存。临时索引在成功、异常或取消退出时关闭并清理。HTML 报告采用增量输出，在线指标读取使用有界缓存；不要在外层重新 `list()` 收集完整证据来抵消这些限制。

## 后处理预算

[PostprocessBudget](../src/rallymate_vision/postprocess_resources.py) 支持两个正值环境变量：

- `RALLYMATE_POSTPROCESS_MEMORY_MB`：默认 **16384 MiB**，实际按 `1024²` 字节换算。
- `RALLYMATE_POSTPROCESS_TIMEOUT_SECONDS`：默认 **1800 秒**，用于分析任务的后处理预算。

API 报告组装显式采用原有报告缓存的准备超时，默认仍为 **300 秒**；修改后处理时间变量不会覆盖这一 API 设置。环境变量在预算对象创建时读取，部署配置后应正常重启服务。

内存采样覆盖当前 API/worker Python 进程：Windows 使用 private bytes，Linux 使用 RSS；不包含独立的 SAM 推理进程，也不是单个请求的独占内存。预算在阶段、记录或数据块之间合作式检查，并传播取消请求。它不是操作系统硬限额，不能抢占没有检查点的函数或保证一次大分配绝不超过阈值。无法读取进程内存时采样返回 0，不能据此判断资源充足。

超限会停止当前后处理并保留原始上传及已生成产物。日志使用 `rallymate.postprocessing` 输出阶段、耗时和采样峰值；管线摘要中的 `postprocessing_resources` 记录预算及采样范围。采样峰值不代表精确的瞬时峰值。

## 离线兼容边界与历史任务

在线报告、运行校验和评分观察器已使用支持池引用的读取方式。校准数据编译及来源资格验证目前只冻结 JSONL/摘要原始字节，尚未冻结外部证据池依赖，因此遇到池引用会明确报出 `pooled evidence requires a frozen dependency snapshot`。旧内嵌数据继续受原有验证规则保护；不能删除引用、跳过校验或直接展开后沿用旧授权哈希。后续接入需把池文件纳入不可变快照、来源授权及提交前复核。

格式升级不会自动重跑历史 `failed` 或 `cancelled` 任务，也不会把失败产物标为成功。历史恢复应另行确认源文件完整性及恢复工具的适用条件，在独立输出目录生成并重新校验；无需重新推理并不意味着已有任意失败任务都能直接恢复。
