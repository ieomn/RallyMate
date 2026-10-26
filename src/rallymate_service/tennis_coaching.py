"""Actionable practice from the measured weak subcriterion of a real episode.

Copy is kept separate from scoring: changing a drill never changes a score. A
recommendation describes its replay segment, not every stroke in the recording.
"""
from __future__ import annotations

import math


def _measurement(metric_id: str, value: float, *, aggregate: bool = False) -> str:
    """Describe the measured quantity, never an unobserved cause or ball event."""
    if type(value) not in (int, float) or not math.isfinite(value):
        return ""
    measures = {
        "knee_load": f"准备时段平均屈膝约 {value:.0f}°",
        "support_control": f"准备时段平均站距约为髋宽的 {value:.2f} 倍",
        "coil": f"准备后半段平均肩髋相对转角约 {value:.0f}°",
        "preparation_sync": f"转体与引拍进入各自前段动作的时间差约占准备时段的 {value:.0%}",
        "coil_release": f"准备后的肩髋相对转角在挥出时减少约 {value:.0%}",
        "head_control": f"头颈相对躯干的位置变化约为躯干长度的 {value:.0%}",
        "arm_space": f"主挥拍时肩到手腕的平均距离约为手臂长度的 {value:.0%}",
        "follow_path": f"挥出后的手腕运行距离约为此前的 {value:.2f} 倍",
        "serve_trophy": f"准备后半段持拍侧上臂与躯干向上的典型夹角约 {value:.0f}°",
        "serve_extension": f"从准备到上挥，同侧膝部最大伸展约 {value:.0f}°",
        "serve_overhead_position": f"主挥拍时持拍手高于肩部约 {value:.2f} 个躯干长度",
        "serve_arm_extension": f"肩到手腕距离约为手臂长度的 {value:.0%}",
        "leg_extension_continuity": f"下肢伸展连贯性为 {value:.0%}，微小角度变化已作保守处理",
        "follow_deceleration": f"主挥拍到首次恢复前，净降速占手腕总速度变化约 {value:.0%}",
    }
    detail = measures.get(metric_id)
    return f"{'测量汇总' if aggregate else '姿态估计'}：{detail}。" if detail else ""


_STRENGTHS = {
    "knee_load": ("准备有屈膝参与", "准备阶段已出现清晰屈膝。", "保持舒适的屈膝准备，自然接上伸展与挥出。"),
    "support_control": ("准备站距舒展", "准备时双脚保持了挥拍所需的站距。", "保持能自然转体和调整的站距，不必把双脚继续拉宽。"),
    "coil": ("引拍有身体转动", "引拍时肩与髋形成了适度的相对转动。", "保持转肩引拍与挥出的连续衔接，不额外追求更大的转角。"),
    "preparation_sync": ("身体与手臂准备衔接紧密", "转体与手臂引拍进入前段动作的时间较接近。", "沿用身体与手臂一起准备的节奏，连续接上挥出。"),
    "release_sequence": ("伸展与挥出衔接顺畅", "下肢伸展、身体转动与主挥拍的先后衔接较顺畅。", "保持伸展、转动与挥出连续相接，不把动作逐段停住。"),
    "coil_release": ("准备转动能接上挥出", "准备形成的肩髋相对转动在挥出阶段得到释放。", "保持引拍后身体继续转动，带着手臂顺势挥出。"),
    "head_control": ("头颈相对躯干平稳", "主挥拍时头颈相对躯干的位置变化较小。", "保持自然头颈姿态，允许舒适转头，不强行固定脖子。"),
    "arm_space": ("手臂有伸展空间", "主挥拍时手臂保持了舒展空间。", "保持手臂自然伸展，不强行锁直肘部。"),
    "follow_path": ("挥出后继续随挥", "主挥拍后手臂继续运行，随挥较连贯。", "沿用挥出后继续随挥的节奏，再自然恢复。"),
    "recovery_control": ("随挥后衔接连续", "随挥后身体转动较连续，并保留了屈膝准备。", "顺着随挥自然转身，再用调整步接回准备。"),
    "serve_trophy": ("持拍臂蓄势舒展", "发球准备时持拍侧上臂自然展开。", "保持舒适的持拍臂蓄势姿态，连续接上上挥。"),
    "serve_arm_coordination": ("双臂蓄势配合清晰", "非持拍臂上伸与持拍臂蓄势形成了配合。", "保持双臂一起完成准备的节奏，不额外追求更高的举臂。"),
    "serve_extension_sequence": ("下肢与上挥相接", "下肢伸展与手臂向上挥出的先后衔接较顺畅。", "保持伸展与上挥连续相接，不刻意憋住某一段。"),
    "serve_extension": ("准备后有自然伸展", "屈膝准备后，同侧下肢出现了明确伸展。", "保持准备后自然伸展，不单纯追求更深的下蹲。"),
    "serve_overhead_position": ("向上挥拍舒展", "主挥拍时持拍手有清晰的向上舒展。", "保持向身体上方自然挥出，随后完成随挥。"),
    "serve_arm_extension": ("上挥保留手臂空间", "向上挥拍时手臂保持了舒展空间。", "保持自然上伸，不强行锁直肘部或额外后仰。"),
    "serve_recovery_control": ("发球随挥与准备相接", "发球随挥后身体转动较连续，并保留了屈膝准备。", "完成随挥后用小步重新准备，延续当前连续节奏。"),
    "leg_extension_continuity": ("下肢伸展连续", "准备后同侧膝部的伸展较连贯，反复屈伸较少。", "保持准备后自然伸展并接上挥出，不强行锁膝。"),
    "follow_deceleration": ("首次恢复前降速连续", "主挥拍到首次恢复前，手腕速度较连续地下降。", "保持顺势随挥，让手臂自然减速，再自然接回恢复动作，不刻意停拍。"),
}


def strength_for_metric(metric_id: str, value: float, *, family: str = "baseline", score: float | None = None, aggregate: bool = True) -> dict[str, str] | None:
    """A positive observation requires a measured, well-executed criterion."""
    if metric_id not in _STRENGTHS or type(value) not in (int, float) or not math.isfinite(value):
        return None
    if type(score) not in (int, float) or not math.isfinite(score) or not 85 <= score <= 100:
        return None
    title, observation, _ = _STRENGTHS[metric_id]
    scope = "综合本次已测动作，" if aggregate else "这段动作中，"
    return {"title": title, "observation": scope + observation + _measurement(metric_id, value, aggregate=aggregate)}


def coaching_for_metric(metric_id: str, value: float, *, family: str = "baseline", score: float | None = None) -> dict[str, str]:
    serve = family == "serve"
    action = "发球" if serve else "挥拍"
    plans = {
        "leg_extension_continuity": (
            "把屈膝准备接成连续伸展",
            "这段准备到挥出之间，未显示清晰的连续伸展。",
            "从舒适屈膝开始，顺势伸展接上挥出；不要来回蹲起，也不必把膝盖锁直。",
            f"做 2 组、每组 8 次半速{action}：屈膝准备后一次连续伸展，接上手臂挥出和完整随挥；组间休息 30 秒。",
            "同机位录 8 次，检查准备到挥出之间是否减少反复屈伸，保持一次连续伸展。",
        ),
        "follow_deceleration": (
            "让随挥顺势自然减速",
            "这段主挥拍到首次恢复前，手腕速度下降的连续性不足。",
            "挥出后顺着原来的方向完成随挥，让手臂自然减速；不要刻意急停或补甩一下。",
            f"做 2 组、每组 8 次半速{action}：连续挥出、自然随挥，再恢复准备；先保持均匀节奏，不额外追求速度。",
            "同机位录 8 次，比较随挥前段是否减少补甩和突然刹停，同时保留完整随挥。",
        ),
        "knee_load": (
            "让下肢参与准备与挥出",
            "这段准备时屈膝较少，可以练习下肢准备与伸展的衔接。" if value < 15 else "这段准备时已有屈膝，下一步让弹性准备更持续地接上挥出。",
            f"准备时保持有弹性的屈膝，随后自然伸展接上{action}，避免蹲住不动。",
            f"做 2 组、每组 8 次半速{action}：先准备，再自然伸展挥出；每次都完成随挥。",
            "用相同机位复测 8 次，检查准备后能否连续伸展，没有停顿或突然直起。",
        ),
        "support_control": (
            "调整脚步，留出准备站距",
            "这段准备时双脚间距较窄，可以先调整到更便于挥拍的站距。" if value < 1 else "这段准备时已有舒展站距，下一步减少准备期间双脚靠拢的时段。",
            "用小步调整，避免双脚挤在一起；按来球自然选择站位，不用刻意把双脚越拉越宽。",
            "同伴在近距离交替喂慢球，先调整站距再轻击。做 2 组、每组 8 球，保持能够自然转体和回位的距离。",
            "用同样喂球复测 8 球，检查准备时是否减少双脚挤在一起，同时能够自然转体和接回下一次准备。",
        ),
        "coil": (
            "让身体参与引拍",
            "这段引拍时肩髋相对转动较少。" if value < 12 else "这段引拍转动较大，练习重点是连续释放。" if value > 60 else "把这段引拍中的身体转动与后续挥出连起来。",
            "以转肩带动引拍，再自然转回挥出，避免只把手臂向后拉；不要强行扭腰。",
            "做 2 组、每组 8 次慢速空挥：双手在身前起始，转肩引拍，再连续完成挥出与随挥。",
            "同机位录 8 次，检查身体准备与手臂引拍是否一起发生，挥出前没有停住等手。",
        ),
        "preparation_sync": (
            "把转体和引拍连起来",
            "这段身体转动与手臂引拍进入前段动作的时间差较大。",
            "看到来球后，让转肩与引拍作为一次准备动作完成，再进入挥拍。",
            "做 2 组、每组 8 次慢球练习：准备、挥出、恢复各连续完成，先降低球速，减少引拍中途停顿。",
            "录 8 次同类击球，检查肩部准备和手臂引拍进入前段动作的时间是否更接近。",
        ),
        "release_sequence": (
            "把下肢、躯干和手臂接起来",
            "这段下肢伸展、身体转动与手臂挥出的衔接不够清晰。",
            "从有弹性的支撑开始，让伸展、转动与挥出连续接上，不要逐段停顿。",
            f"做 2 组、每组 8 次半速{action}：准备后连续挥出，完整随挥，再恢复准备。先求顺畅，再提高速度。",
            "同机位复测 8 次，查看下肢伸展后躯干与手臂是否顺势接上，减少身体停住后才挥手的情况。",
        ),
        "coil_release": (
            "把准备的转动释放出去",
            "这段准备后的肩髋相对转动，在主挥拍中释放较少。",
            "引拍后让身体转动继续带动手臂，保持顺势挥出，不要在准备姿态上停住。",
            "做 2 组、每组 8 次半速空挥，从转肩准备连续做到随挥；让同伴观察躯干是否中途停住。",
            "用相同机位复测，检查主挥拍时身体与手臂是否继续一起运动。",
        ),
        "head_control": (
            "减少挥拍时头颈晃动",
            "这段主挥拍期间，头颈相对躯干的位置变化较明显。",
            "保持自然视线和头颈姿态，减少挥拍时突然侧摆；不要强行固定脖子。",
            "做 2 组、每组 8 次低速喂球练习，维持舒适的头颈姿态，完整挥拍后再恢复。",
            "同机位复测 8 次，对比主挥拍期间是否减少突然侧摆，同时保持自然转头。",
        ),
        "arm_space": (
            "给手臂留出挥拍空间",
            "这段主挥拍时手臂伸展空间偏小。" if value < .65 else "这段主挥拍时已有手臂伸展，下一步更持续地保留舒展空间。",
            "先用脚步调整与来球的距离，让手臂自然展开；不必强行锁直肘部。",
            "用近距离慢球做 2 组、每组 8 球：小步调整，保持身体与挥拍手臂之间的自然空间，再完整随挥。",
            "同机位复测 8 球，记录主挥拍时手臂保持自然舒展的次数，先争取 6 次，再保持到 8 次。",
        ),
        "follow_path": (
            "挥出后继续完成随挥",
            "这段主挥拍后的手臂延展与连续性，是本次优先改进的环节。",
            "让手臂顺势完成随挥，再恢复准备，不要在挥出后马上刹住。",
            f"做 2 组、每组 8 次半速{action}，每次连续完成挥出和随挥；动作自然结束后再开始下一次。",
            "用相同机位复测 8 次，检查手臂是否继续顺势运行，减少突然停拍。",
        ),
        "recovery_control": (
            "随挥后接回下一次准备",
            "这段随挥后的动作连续性与下一次准备衔接，是优先练习的环节。",
            "顺着随挥自然转身，用调整步接回可启动的准备姿态，减少突然反复折返，不必先停住身体。",
            "同伴连续喂 8 个慢球，每球把随挥、自然转身与调整步连起来。做 2 组，速度以能连续接上下一次准备为准。",
            "同样喂球复测 8 球，检查随挥到准备是否连续，减少突然来回摆动，并记录及时进入下一次准备的次数。",
        ),
    }
    if serve:
        plans.update({
            "support_control": ("自然站位，连续完成发球", "这段发球可从自然站位与连续蓄势开始练习。",
                "沿用舒适的发球站距，把屈膝准备与向上挥出接起来。",
                "做 2 组、每组 8 次半速发球空挥：站稳、蓄势、向上挥出、随挥后恢复。",
                "同机位复测 8 次，记录蓄势与上伸能够连续完成的次数。"),
            "serve_trophy": ("建立舒展的持拍臂蓄势", "这段发球准备时，持拍侧上臂的蓄势位置是优先练习项。",
                "让持拍侧上臂自然展开，保持肩部舒适，再连续向上挥出。",
                "做 2 组、每组 8 次慢速发球空挥：双臂配合进入蓄势姿态，再向上挥出，不强压肩肘。",
                "同机位复测 8 次，检查持拍侧上臂是否自然展开并顺畅衔接上伸。"),
            "serve_arm_coordination": ("配合好发球准备的双臂", "这段非持拍臂上伸与持拍臂蓄势的配合不足。",
                "让双臂配合完成准备，再连续向上挥出；不用刻意追求更高的举臂。",
                "做 2 组、每组 8 次慢速发球准备，双臂配合进入蓄势后连贯空挥，先熟悉节奏再加入球。",
                "录 8 次发球，检查双臂是否在准备阶段形成稳定配合，没有一只手过早结束。"),
            "serve_extension_sequence": ("衔接下肢与向上挥拍", "这段下肢伸展与手臂上挥的衔接不够清晰。",
                "从屈膝蓄势自然伸展，让手臂向上挥出跟上身体节奏，减少中途脱节。",
                "做 2 组、每组 8 次半速发球空挥，连续完成蓄势、上伸、随挥，不刻意憋住某一段。",
                "同机位复测 8 次，比较下肢伸展与手臂上挥之间的衔接是否更连贯。"),
            "serve_extension": ("用自然伸展带动上挥", "这段发球准备后的下肢伸展幅度较小。",
                "在舒适屈膝后自然向上伸展，再完成挥拍；不要单纯把下蹲做得更深。",
                "做 2 组、每组 8 次半速发球空挥，感受准备后顺势伸展，保持完整随挥。",
                "同机位复测 8 次，检查准备后是否出现连贯伸展，而非保持屈膝只挥动手臂。"),
            "serve_overhead_position": ("让挥拍向上舒展", "这段主挥拍时，持拍手向上舒展的空间较少。",
                "让手臂向身体上方自然挥出，再顺势随挥，避免只在身体前侧推拍。",
                "做 2 组、每组 8 次半速发球空挥，连续完成蓄势、向上挥出与随挥，不强行后仰。",
                "同机位复测 8 次，检查主挥拍是否有清晰的向上舒展阶段。"),
            "serve_arm_extension": ("保留向上挥拍的手臂空间", "这段向上挥拍时，手臂伸展空间偏小。",
                "让持拍臂自然向上伸展，保持舒展空间，不强行锁直肘部。",
                "做 2 组、每组 8 次半速发球空挥，在舒适范围向上挥出并完整随挥。",
                "录 8 次同类发球，对比向上挥拍时手臂是否更舒展。"),
            "serve_recovery_control": ("发球后重新建立准备", "这段随挥后的动作连续性与重新准备是优先练习项。",
                "完成发球随挥后，用小步调整到能够继续移动的准备姿态。",
                "做 2 组、每组 8 次：发球空挥、自然随挥、小步调整、重新准备，再开始下一次。",
                "同机位复测 8 次，检查发球后是否能够从容接上下一次移动。"),
        })
    fallback = ("连贯完成这段动作", "这段动作是本次优先练习的环节。", f"用较慢节奏连续完成准备、{action}和随挥。",
                f"做 2 组、每组 8 次半速{action}，每次都完成随挥与恢复。", "用相同机位复测 8 次，比较这一环节的连续性。")
    plan = plans.get(metric_id, fallback)
    result = dict(zip(("title", "observation", "action", "drill", "retest"), plan))
    positive = strength_for_metric(metric_id, value, family=family, score=score, aggregate=False)
    if positive:
        # A strong criterion needs a maintenance plan, not a defect title above
        # a positive observation with instructions to exaggerate the movement.
        result.update(title=f"巩固：{positive['title']}", observation=positive["observation"],
                      action=_STRENGTHS[metric_id][2],
                      drill=f"做 2 组、每组 8 次半速{action}，保持这一环节的节奏；组间休息 30 秒，再重复一次。",
                      retest="同机位录 8 次同类动作，目标是至少 6 次保持这一环节的表现，再逐渐增加速度。")
    else:
        result["observation"] += _measurement(metric_id, value)
        if "6 次" not in result["retest"]:
            result["retest"] += "先以 8 次中至少 6 次做到为目标。"
    return result
