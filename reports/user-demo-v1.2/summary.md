# RallyMate 用户端 Demo v1.2 字段与边界留存

## 结论

当前用户结果已升级为 `schema_version=1.2.0`、
`result_version=rallymate-user-demo-result-v1.2.0`。普通用户页面以
`training_evaluation` 的“动作表现参考分（Beta）”为主，严格只展示 FS01 4 项、FS02 4 项和
FS09 5 项共 13 项。每项提供自然语言观察、证据摘要、训练建议和限制；每类动作另有
`actions[].performance_assessment`。

Beta 分只用于本次视频训练复盘，不是准确率、正式技术分、教练标定分或 A～E。
`formal_scoring.available=false`，正式 `score_0_to_100` 和 `grade` 均为 `null`。
若某个单项、动作或总评没有足够证据，其分数保持 `null`，页面显示“暂无法评价”，不得以 0
代替 unavailable。

精确字段、13 项 allowlist、权重、空值语义、兼容字段、运行设备和浏览器边界见
[demo-contract.json](demo-contract.json)。

## v1.1 历史兼容

M96 v1.1 的 `final_demo_score`、`analysis_quality`、`display_score`、
`actions[].formation_assessment`、`summary_zh` 和幅度字段仍保留在响应中，供旧客户端和历史
重放使用；普通用户页不再把“动作信息成型参考分”作为技术表现主分。历史证据原样保存在
`reports/m96-user-demo-v1.1/`，本记录不覆写其合同、字段记录或 SHA。

## 运行设备与页面边界

结果新增 `runtime.accelerator`、`device_name`、`device_used`、`pose_backend`、
`pose_profile`、`cpu_thread_limit` 和 `annotated_video_generated`。默认路径在 GPU 上运行
RTMPose-M Halpe26 realtime；CUDA 不可用时回退 CPU。Worker 以显式 `-CpuThreads`、环境变量
`RALLYMATE_CPU_THREADS`、默认 4 的顺序设置 Torch、OpenCV 和常见数值计算池。该值不是整个
进程或 FFmpeg 的绝对总线程数。

旧成功任务可能没有落盘 `cpu_thread_limit`，所以 v1.2 适配器允许该字段为 `null`；新任务才会
写入实际配置，默认 4。普通用户页面默认关闭标注视频以降低 CPU 编码开销；API
`write_annotated_video` 为兼容旧客户端仍默认 `true`。

页面不展示 raw job/result JSON、scoring-loop 或 analysis 内部报告链接，也不透出
`job.error` 或异常正文。页面只在标注视频实际生成时提供骨架视频链接。CSS/JavaScript 使用
`?v=1.2.0` 防止旧缓存；`/?job_id=<UUID>` 只在 UUID 校验通过后恢复已有任务，并仍只保存不敏感
job ID。

## 真实结果快照

本机成功任务 `d7c617d6-89d2-44f2-83e5-de25cb24b689` 已由当前 v1.2 适配器重放：

- Beta 总分：78/100；
- 已评价指标：13/13；
- FS01 / FS02 / FS09：78 / 77 / 78；
- 运行设备：GPU NVIDIA GeForce RTX 5070 Ti；
- Pose：RTMPose / realtime；
- 正式教练分和 A～E：`null`。

该任务生成时尚未记录 CPU 线程字段，因此 `runtime.cpu_thread_limit=null`；它曾显式生成标注视频，
不代表当前页面默认开启。这一快照只证明真实产物能够形成 v1.2 Beta 训练反馈，不构成准确率、教练
标定、F3/F4 或模型晋级证据。

## 验证

当前源代码稳定后实际执行：

- `python -m unittest -v tests.test_training_evaluation tests.test_user_demo tests.test_service tests.test_worker_cpu_threads`：31/31 通过；
- `python -m py_compile ...`：相关实现和聚焦测试通过；
- `node --check src/rallymate_service/assets/user-demo.js`：通过；
- 两个 Worker PowerShell 启动脚本语法解析：通过；
- `demo-contract.json` 严格 JSON 解析：通过。

`demo-contract.json` 当前 SHA-256 为
`c8d1f87b940d2ef880d1e6b426b3565cd98b19b43ef3c08a7d920fd92e4fd88d`。
实现及测试文件的最终字节数和 SHA-256 固化在
[field-change-record.json](field-change-record.json)。字段记录自身的 SHA-256 在文件外计算并报告。
