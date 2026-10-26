# MIT M09 手—球拍可观测性修订的独立真实重放

本批使用已观察的同一 MIT 正手开发材料、同一原始姿态记录和四个人工 M09 候选窗：40–45、103–107、162–166、224–229，共 22 个原帧。窗口、原 PTS、非专家边界及不确定性均未修改；没有新增视频、独立保留验证、推理、训练或阈值选择。人工候选并不证明拍球接触、持拍接触或重新控制球拍。

新运行时为已提交的 `64c097d9c8ae0bf7fb247b00648204b75519b26b`。新 producer 为 `scripts/replay_gs_hand_racket_revision.py`，仅复用旧脚本的 annotation 构建和原始来源校验，不调用旧 `replay()`，不把新结果写回历史 fixture。

## 实际结果与范围

- M09 子报告：20 个实例，3 个正式应用条件分、17 个不可评分。GS01-M09-01/02/03 的 12 个实例与历史结果整行相同，包括条件、测量、窗口和来源账本。
- GS01-M09-04/05 的 8 个实例均为 `unavailable`，`conditions=[]`，grade/proxy_grade/score/original_grade 均为空。两卡具有不同的缺失观测要求与原因，不能以相同双腕距离变化充作两个独立行为。
- 双腕测量整行保留在 `auxiliary_observations.measurements.wrist_separation_torso`，且 `scoring_eligible=false`。首窗 delta 仍为 `-0.0846946610547554`；后三窗 delta 仍为空，未延长窗口填值。完整原帧分母依次为 6、5、5、6。
- 实际统一服务调用输出 298 张目录卡、75 个实例：3 有分、72 无分。20 个 GS 实例与直接 adapter 完全一致，其余 55 个既有 FS 实例均无分。本次仅 GS01-M09-01 有分，3 次均为 A100；统一按卡平均为 100，覆盖仅 1/298，不能解释成整体技术表现 100。

GS01-M09-04 缺少非持拍手相对球拍的来源观测；GS01-M09-05 缺少手—球拍重新控制的连续证据。已有 raw 只提供球拍 bbox/中心和 COCO 身体点，没有独立 handle/head/throat/stringbed 点、手—球拍关联或接触/握持证据。两卡数值缺失不能记作 0 分，也不能由辅助双腕数据恢复正式分。

## 不变来源与新输出

源 run 为 `.codex_tmp/gs-card-validation-20261022/runs/card-ocw-pe.710-2-forehand-220k`；视频为 `.codex_tmp/card-video-validation-20261022/media/ocw-pe.710-2-forehand-220k.mp4`，SHA256 为 `3c3dbc120084e7ec1341e5ee0db4ec403fa553204d0ca92d06e9138d7bb71a50`。

冻结窗口文件 `tests/fixtures/gs-follow-through-windows-20261024.json` 的 SHA256 为 `cd88316d09b64d94bd341377e3250f28919513c740da3ea72ea5c7888e79a3bb`；历史结果 `gs-follow-through-results-20261024.json` 的 SHA256 为 `c7e309ac3f611207ab0a2ae18ff67af0c3d684be5dbb3f8bc98306736226b7ff`，两者原样保留。

这两个摘要指本次 `64c097d` 干净 archive 实际读取的文件字节；该归档中 raw SHA 与 UTF-8 去 BOM、CRLF/CR 转 LF 后的 SHA 相同。Windows 工作树若自动转换行尾，raw SHA 可以不同；不能据此判断历史被修改。视频、JSONL、审图文件的摘要始终按原始字节计算，运行时源文件同时记录 raw 与 LF 两种口径。

本批新 fixture 为 `tests/fixtures/gs-hand-racket-revision-results-20261024.json`，包含全部 20 个实例、4 个原窗、逐帧 source/PTS 账本、20 项历史比较、4 个原始输入文件 SHA、原图/边界材料 SHA，以及实际加载的 89 个运行时源文件/目录文件的 raw 和 UTF-8 LF SHA。新 fixture 保存的是新执行结果，不是替换旧 status。

独立完整文件保留在忽略目录 `.codex_tmp/gs-m09-hand-racket-revision-20261024/committed-runtime-replay-final/`：

- `full-gs-report.json`：`877607efb35ca94b373b08b0f55b7dd22b68cd79585b4ac9057c0dc492da9ef8`。
- `gs-stage-annotations.json`：`0eb5b2f55cfb415cbd0c5a67475e73930087f8efcb3ba78f08c06a73801b24d9`。
- `unified-current-catalogue.json`：`e5da522768d0b3016dbc92224ae06668ca821504543b3fb7c3be577ca59beebf`。
- `source-closure-receipt.json`：`6837ff9a2d187300ebdca49eca578a52a889801bc9c24629d5969860e8b8b34c`。

此前冻结 backend candidate 的独立实际运行仍留在同目录的 `actual-replay/`，其收据 SHA 为 `c2edef062a130dfe75d07ba6d60b6836d8259090fe9ac75c4710dd046d5bf21d`。新提交运行时重新解码和重算后，上述三个完整输出与该候选运行逐字节相同。没有把两次重放算成两组视频验证。

## 复现入口

从包含本批四文件的仓库根目录执行以下 PowerShell 命令。必须提供原视频、raw 和冻结审图材料；目录缺失时完整媒体重放会失败，不会使用旧分数顶替。输出目录必须不存在。`--artifact-root` 只提供不可变来源，生产 imports 必须来自指定干净 runtime。

```powershell
$taskRepo = (Get-Location).Path
$taskBase = Join-Path $taskRepo '.codex_tmp/m09-revision-independent'
$taskRuntime = Join-Path $taskBase 'checkout'
New-Item -ItemType Directory -Path $taskRuntime
git archive --format=tar --output="$taskBase/runtime.tar" 64c097d9c8ae0bf7fb247b00648204b75519b26b
tar -xf "$taskBase/runtime.tar" -C $taskRuntime
Copy-Item scripts/replay_gs_hand_racket_revision.py "$taskRuntime/scripts/"
python -B "$taskRuntime/scripts/replay_gs_hand_racket_revision.py" --artifact-root "$taskRepo" --repository-root "$taskRepo" --output-directory "$taskBase/output"
```

runner 会 fresh decode 原视频的真实 PTS，校验原图/边界文件 SHA、raw SHA、逐帧原窗和来源，再执行直接 GS adapter 与统一服务。统一服务只在独立同名 run 副本中附加 annotations。若媒体目录迁移，只修改副本 summary 的视频路径，并将副本 annotation 的 summary SHA 重绑定；原 summary 和原 annotation 不改写。迁移证据保留两份 summary SHA，以及两份 annotation 的 raw/canonical SHA。统一实例比较只允许 `source_annotation.document_sha256` 随已验证的新 annotation 改变，其余整行必须相同。本次主重放无需迁移，另根实际测试覆盖了迁移分支。

独立审查发现初版 runner 在迁移 summary 后未重绑定 annotation SHA，会被严格 adapter 拒绝；该失败及中间输出保留在忽略目录，未当作通过。当前修订通过同一另根反例，未放宽来源验证，也未修改任何生产源文件。

每个已加载的生产文件与指定 commit 的规范化源字节比较，同时记录实际 raw SHA；runner 自身作为独立 producer 另记 SHA，不声称其在旧 commit 中。新报告写入前后均检查来源文件不变。这里验证的是保存观测的重新计算与真实媒体时间/身份闭包，没有重新执行 YOLO/SAM。

## 缓存与测试

缓存诊断在同一进程、同一副本输入上实际计算归档 `add4267` 目录，再重复调用取得一次 hit；恢复新目录后不 clear cache，取得第二次 miss，且新统一报告完整还原。旧、新 canonical catalogue digest 分别为 `1c41382a15166ca653e36197e1bcffc963361770633681d14c181422c3439aff` 和 `0f502fa1ce6ebc557ea466db3811864fbf94648ee9269b4efc7ba763f4d053b8`。归档目录报告只属于缓存诊断，不计入本批正式结果。

`tests/test_gs_hand_racket_revision.py` 在干净归档上 10 项通过、0 跳过：原录制轨迹重算、完整辅助数值保持、旧分/条件恢复拒绝、辅助值篡改拒绝、未修订卡改值拒绝、来源账本变化拒绝、重复实例拒绝、旧输出保护、实际原媒体加统一服务/缓存重放，以及相同媒体逐字节复制到另根后的副本重绑定。媒体测试使用 `CARD_ARTIFACT_ROOT` 和 `CARD_REPOSITORY_ROOT`；无原媒体的环境会明确跳过两项媒体测试，其余录制轨迹测试仍可执行。没有修改历史 fixture，也没有增加真实技术识别准确率声明。
