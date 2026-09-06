# M96 用户端 Demo v1.1 实现报告

## 结论

M96 已把用户端 Demo 的结果协议升级为 `schema_version=1.1.0`。用户可以选择一个或多个原视频，浏览器按顺序为每个视频创建一个既有单任务 API 作业，并显示动作轮廓、幅度信息、每类动作的信息成型判断、动作信息成型参考分和独立的分析完成度。

`final_demo_score` 只表示“当前视频中可识别的动作轮廓与幅度信息成型程度”。它不是球员技术水平、教练评分、识别准确率，也不是正式 A～E。正式评分始终不可用，总分和等级均为 `null`。

## 字段关系

- `final_demo_score`：新的主展示字段，范围为 0–100；由 FS01、FS02、FS09 三类动作的 `formation_assessment.reference_score_0_to_100` 做无权重平均得到。
- `analysis_quality`：单独保留的分析完成度，描述动作片段和可测特征的完整程度。
- `display_score`：为旧客户端保留的兼容字段，值与 `analysis_quality` 相同，并通过 `compatibility_alias_for=analysis_quality` 明示关系。
- `actions[].formation_assessment`：返回机器状态、中文标签、0–100 信息成型参考分、中文含义与解释、组成项、权重和版本。
- `actions[].summary_zh`：已接入普通用户页面，不再只存在于 JSON。

具体字段、组成权重、状态阈值和语义边界见 [demo-contract.json](demo-contract.json)。

## 批量提交与刷新恢复

文件输入支持 `multiple`。前端严格按用户选择顺序逐个调用原有 `POST /v1/jobs`，轮询当前作业并获取 `/demo-result`；后端单任务 API 没有改成批量接口。页面显示“第 X/Y 个”，保留每个已完成视频的简要结果，并允许切换回详细结果。

浏览器 `localStorage` 只保存版本、已提交/完成/失败/活动任务 UUID 和批次索引、总数。访问口令、Authorization、文件对象、文件名、视频内容和结果载荷均不保存。刷新后可以恢复已经提交的作业；刷新前尚未提交的本地文件必须重新选择。需要鉴权时，用户必须重新输入访问口令。

## 旧任务行为

成功状态的旧任务如果缺少 `indicator-features.jsonl`，`GET /v1/jobs/{job_id}/demo-result` 返回 HTTP 409，错误码为 `legacy_job_requires_reanalysis`，并要求重新选择原视频分析；该情形不再返回 500。

## 验证记录

以下结果来自本次实现后实际执行，不是推测值：

- `python -m py_compile`：通过，覆盖两个后端实现文件和两个聚焦测试文件。
- `node --check src/rallymate_service/assets/user-demo.js`：通过。
- `python -m unittest -v tests.test_user_demo tests.test_service`：20 项测试全部通过。
- 本地服务静态页面探测：HTTP 200，并确认 multiple、参考分、批次区域、`formation_assessment`、`summary_zh` 和恢复逻辑均由服务返回的资源包含。

聚焦测试覆盖协议版本、两个分值的隔离关系、每动作成型判断、正式 A～E 不透传、未知指标不得抬高参考分、批量/恢复静态契约、完整上传到结果接口闭环，以及缺少指标文件时的 409 行为。

## 审计与复核

七个实现及测试文件的字节数和 SHA-256 固化在 `field-change-record.json`。该记录由 `scripts/build_m96_user_demo_field_record.py` 生成；脚本使用独占创建模式，目标文件已存在时拒绝覆盖。记录自身的 SHA-256 必须在文件外计算并报告。

本报告没有修改或替代产品文档、技术文档、API 文档、README 或 Draw.io。
