# RallyMate 主球员时序基线

版本：`primary-player-baseline-2026-08-13.1`  
语义主球员：所有视频/事件区间使用稳定 `primary_player_id=1`；原始 Track ID 作为可追溯来源保留。

> source Track 切换、左右交换和跳点均为诊断候选，不是有身份/关键点真值的准确率。

| 视频 | Track 覆盖 | Pose 覆盖 | 来源 Track 数 | 来源切换候选 | 关键点有效率 | swap 帧 | jump 帧 | 最长 Pose 缺失 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `3ae77ee3271d67de171585a5c39ddd69` | 97.15% | 96.46% | 6 | 6 | 94.33% | 16 | 96 | 41 帧 / 1367 ms |
| `850cb0006b406c7176eeda8d711cd065` | 99.66% | 88.32% | 11 | 14 | 93.98% | 62 | 119 | 147 帧 / 4900 ms |
| `8d7754d0de6d315674013d5b69a0b6ba` | 99.97% | 97.16% | 14 | 31 | 99.88% | 587 | 790 | 61 帧 / 2542 ms |

选择器综合 Track 全区间覆盖、Pose 有效率、运动量、框面积和逐帧空间连续性；框面积权重仅 10%，不再使用逐帧最大框直接决定主球员。

`confirmed_id_switch_count` 在没有人工身份真值时保持 `null` / `ground_truth_required`。事件层应使用 `primary_player_id`，并通过 `source_track_id` 回溯原检测和 Pose。
