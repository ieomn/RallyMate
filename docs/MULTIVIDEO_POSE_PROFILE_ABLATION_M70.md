# M70 多视频 Pose profile / 裁剪上下文消融

## 结论

M70 证明“更高分辨率模型”和“更大/更宽松的 ROI”对评分特征的影响不能混为一谈。只把 M68 实际裁剪上下文中的 Pose profile 换为 RTMPose-M Halpe26 384×288，三视频只恢复 2 个 operational 指标实例；M69 的统一 0.30 margin/8px 上下文恢复 6 个。

直接从两个候选中选择必需关节有效数最多的结果，虽然总恢复仍为 6、相对 M68 没有回归，却丢失了一个 M69 已恢复实例，并在别处补回一个。总数相同不代表逐事件安全，因此该路由被拒绝。

当前最佳工程候选是 M69 锚定扩展：以 M69 的帧为 baseline，只接受同上下文 384×288 候选对 required joints 的严格有效集超集。它选择 15 个扩展帧，保留 M69 全部恢复，并额外恢复一个 operational 实例；但仍不进入生产。

## 三种策略的机器结果

- 同上下文 384×288：32 帧被采用；feature 恢复 4，operational 恢复 2。
- M69 统一上下文：40 帧被采用；feature 恢复 6，operational 恢复 6。
- 直接多候选择优：42 帧被采用；feature 恢复 8，operational 恢复 6；丢失 1 个 M69 operational 恢复，拒绝。
- M69 锚定扩展：额外采用 15 帧；相对 M68 feature 恢复 9、operational 恢复 7，两个口径回归均为 0；相对 M69 operational 新增 1、丢失 0。

锚定后的投影是 2,338/2,366 feature-vector complete、2,327/2,366 operational measured。18 条 measurement hard fail 不变；21 条非 hard-fail 残差继续保持 unavailable，不填 0。

## 动态证据

- 视频 1：`reports/measurement-recovery-m70/m69-anchored-extension-v1/3ae77ee3271d67de171585a5c39ddd69/m69-vs-anchored-profile-extension-changed-browser.mp4`
- 视频 2：`reports/measurement-recovery-m70/m69-anchored-extension-v1/850cb0006b406c7176eeda8d711cd065/m69-vs-anchored-profile-extension-changed-browser.mp4`
- 机器汇总：`reports/measurement-recovery-m70/summary/report.json`

视频 3 的锚定扩展选择 0 帧，因此没有生成一段虚假的“模型变化视频”。两段实际视频均为 H.264，57/58 帧；浏览器实测 `readyState=4`、`error=null`。

## 安全边界

候选选择只读取注册表必需关节的逐帧有效集合，不读取特征数值、事件结果、评分状态、grade 或 threshold。事件边界、质量门禁和评分公式均未修改。

当前没有人工校正关键点、分视角特征误差或预注册独立视频发布结果，因此这些数字只代表当前三视频固定边界上的可观测性，不代表关键点准确率、事件准确率或 A～E 评分能力。生产配置保持不变，13 项继续为 F2，所有结果仍为 `calibration_required` 或 `unavailable`。
