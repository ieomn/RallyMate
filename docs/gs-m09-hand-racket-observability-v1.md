# GS01 M09 手—球拍必要观测修订

本修订只影响 `GS01-M09-04` 和 `GS01-M09-05`。原文分别要求“非持拍手主动接近球拍”和“球拍重新进入非持拍手控制”（源表 T047、T048）。过去两卡共用双腕距离下降条件，该人体几何量不能独立证明这两种手—球拍行为。原卡文字、等级、M09 边界及历史验证文件保持原样。

目录全局仍为 `card-rules-v1.0.0`，仅这两卡标记 `scoring_revision_id: gs01-m09-hand-racket-observability-v1.0.0`。两卡 `evaluation.kind` 为内部的 `manual_review` 类型，`conditions` 为空；当前执行直接返回 `unavailable`，`grade`、`proxy_grade`、`original_grade`、`score` 均为空，不发出人工复核请求。其余 296 卡的目录对象及评分逻辑不变。

## 两种缺失证据

- M09-04：`non_racket_hand_to_racket_source_required`。需要同源非持拍手身份、可定义的球拍几何位置、连续手到球拍接近关系；分别列为 `source_bound_non_racket_hand_identity`、`source_bound_racket_geometry`、`continuous_hand_to_racket_approach`。
- M09-05：`hand_racket_control_sequence_required`。需要同源非持拍手身份、拍柄或明确接触区域、重新接触或控制时序；分别列为 `source_bound_non_racket_hand_identity`、`source_bound_racket_handle_or_contact_region`、`hand_racket_recontact_or_control_sequence`。若未来依靠接触后的共同运动建立证据，必须另行绑定支持窗，不能扩大原 M09 窗口。

以上分别保存在目录 `required_observables` 和结果 `missing_required_observables`。当前 COCO 姿态没有抓握点；球拍检测框不提供拍柄、握持或归属关系。已有人工测量腕侧也不能补出持续持拍/非持拍手真值。因此本批没有给这两卡新造替代条件或阈值。

## 附属腕距测量

源窗口有效时，执行器仍调用 `measure_card_window` 从原始样本重算，必要共同关节为双肩、双髋、双腕，分母保留整个已绑定原窗。仅抽取原 `wrist_separation_torso` 测量行，放到独立字段：

```json
{
  "auxiliary_observations": {
    "version": "gs-hand-racket-auxiliary-v1.0.0",
    "semantics": "bilateral_wrist_geometry_only_not_hand_racket_approach_or_control",
    "measurement_version": "card-kinematics-v1.0.0",
    "scoring_eligible": false,
    "status": "measured_auxiliary",
    "measurements": {"wrist_separation_torso": "完整原测量行"},
    "reason_codes": []
  }
}
```

实际测量行保留源帧、有效帧、分母、覆盖率、几何门槛、原单位和全部统计。上述字符串只是字段位置说明，生产输出为完整对象。附属证据不包含条件 `passed`、等级或分数，也不使用 M01 的 `auxiliary_evidence` 类型。

沿用原静态距离规则：完整窗至少 6 帧才计算首尾差，每端取 `max(3, ceil(n*0.2))` 帧中位数。少于 6 帧或共同关节缺失时可保留真实静态统计，但不补 `delta=0`；附属状态为 `unavailable`。身份、时钟、相机或源阶段绑定错误保留具体原因。服务在源预验证阶段失败时只返回 revision、缺失观测和源错误，不伪造附属测量。

## 历史隔离与缓存

目录的 canonical JSON SHA 已是统一服务内存缓存键的一部分；这两卡的目录改动使旧结果缓存失效。API 还绑定规则文件摘要及报告构建代码指纹；进程已加载规则后若文件改变，需要重启，不能混用新旧规则。无需全局提升评价器或 GS runtime 版本。

前端必须识别这两卡专属 revision。没有该 revision 的历史双腕条件可保留为已废止的历史证据，但不能作为修订后的分数；不能因为 kind 变化丢掉两行。其它 296 卡不受这项历史兼容处理影响。前端接入与本后端修订应一同交付。

## 验证边界

`tests/test_gs_m09_observability.py` 使用数学轨迹验证完整强收腕仍不出分、6/5 帧门槛、共同关节分母、缺源/错人/时钟/阶段拒绝及外部保存分数无效；这些数学轨迹不算真实视频阳性。`tests/test_gs_follow_through_validation.py` 继续读取未修改的历史 4 窗证据：40–45、103–107、162–166、224–229。其余 3 卡的 12 实例逐字段不变，两卡 8 实例的附属测量逐字段等于历史条件证据；当前结果从 5 个有分实例变为 3 个，其余 17 个保留缺数据状态。真实媒体重放另外核验原视频、原输入、实际解码时钟及当前代码来源，不改写历史 producer 收据。
