"""Audit saved per-card measurements and independently verified complete rules.

Complete grades require a trusted, code-registered per-card verifier that runs
against the source and evidence. JSON grade claims alone cannot promote a card.
Measurements, card links, observations and display samples use separate counts.
"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
QA = ROOT / '.codex_tmp/autodl-sam-coordination-20261018'
EXPECTED_DIMENSIONS = {'movement': 70, 'preparation': 78, 'swing': 72, 'contact': 25, 'follow_through': 53}
SOURCE_BLOCKERS = {'source_rule_conflict', 'source_boundaries_missing'}
# Populated only when a source-grounded evaluator has an independent executable
# verifier. Never load verifier code or trust verification flags from report JSON.
# A verifier(source, card, observations_by_id, raw_evidence_by_id) returns the
# complete-source-grade-verification-v1 proof validated by verify_complete_grade.
COMPLETE_RULE_VERIFIERS = {}

# This is a source-condition inventory, not an implementation of these rules.
# A newly linked partial feature is accepted only from the assessment producer;
# this audit never manufactures additional card links from these names.
SUBCONDITIONS = {
    'FS01-M01': [
        ('head_center_motion', '无球时仅保留头部稳定相关量：鼻及双耳中心位移速度', ['head_center_mean_speed_frame_long_edge_s'], 'source_permitted_partial_head_measurement'),
        ('head_axis_variability', '无球时仅保留头部稳定相关量：双耳轴角波动', ['head_axis_circular_std_deg'], 'source_permitted_partial_head_measurement'),
        ('ball_attention_and_timing', '来球关注与动作时机需要独立球和完整时序证据', [], 'not_implemented'),
    ],
    'FS02-M01': [
        ('launch_direction', '启动方向', ['launch_direction_deg'], 'existing_feature_requires_source_binding'),
        ('incoming_ball_direction_agreement', '身体中心启动方向与球轨迹方向夹角', [], 'not_implemented'),
    ],
    'FS03-M03': [
        ('ankle_order_exchange', '左右踝在移动轴上的相对顺序发生交换', [], 'not_implemented'),
        ('ankle_trajectory_crossing', '轨迹交叉', [], 'not_implemented'),
        ('stride_over_hip_width', '步幅/髋宽比例', [], 'not_implemented'),
    ],
    'FS04-M04': [
        ('ankle_distance_curve', '踝距变化曲线', [], 'not_implemented'),
        ('ankle_order_not_exchanged', '双踝顺序不交换', [], 'not_implemented'),
        ('following_foot_delay', '跟随脚位移延迟相对移动脚的时序', [], 'not_implemented'),
    ],
    'FS09-M03': [
        ('braking_speed_decrease', '髋中心速度下降率', ['hip_center_deceleration_body_s2', 'hip_center_speed_drop_body_s'], 'existing_event_proxy_requires_post_braking_window'),
        ('bilateral_knee_flexion', '双膝屈曲变化', ['left_knee_flexion_change_deg', 'right_knee_flexion_change_deg'], 'existing_event_proxy_requires_post_braking_window'),
        ('hip_descent', '髋中心下移量', ['hip_height_delta_body'], 'existing_relative_ankle_proxy_requires_reference_check'),
        ('trunk_stability', '躯干角波动', ['torso_lean_variability_deg'], 'existing_event_proxy_requires_post_braking_window'),
    ],
}


class MatrixAuditError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise MatrixAuditError(message)


def read(path: Path):
    def reject(value):
        raise MatrixAuditError(f'Non-finite JSON number in {path.name}: {value}')
    return json.loads(path.read_text(encoding='utf-8-sig'), parse_constant=reject)


def digest(path: Path):
    hasher = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            hasher.update(block)
    return hasher.hexdigest()


def rows(path: Path):
    if not path.exists():
        return []
    with path.open(encoding='utf-8') as stream:
        return [json.loads(line) for line in stream if line.strip()]


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def unique_by(items, key, label):
    result = {}
    for item in items:
        identifier = item.get(key)
        require(isinstance(identifier, str) and identifier, f'{label}: missing {key}')
        require(identifier not in result, f'{label}: duplicate {identifier}')
        result[identifier] = item
    return result


def ratio(numerator, denominator, meaning):
    return {'numerator': numerator, 'denominator': denominator,
            'fraction': numerator / denominator if denominator else None, 'meaning': meaning}


def verify_complete_grade(source, card, observations, evidence, verifier):
    """Run trusted code, then bind its proof to this exact source and run evidence.

The verifier must independently evaluate every source condition, applicability,
unevaluable gate and grade choice. This boundary does not interpret natural
language or invent thresholds, and does not accept a report-supplied proof.
"""
    identifier, grade = source['indicator_id'], card['grade']
    require(callable(verifier), f'No complete source-rule verifier: {identifier}')
    require(not source['source_blockers'], f'Source-blocked formal grade: {identifier}')
    selected = grade.get('grade')
    require(selected in {'A', 'B', 'C', 'D', 'E'} and grade.get('value') in {None, selected},
            f'Unsupported formal grade: {identifier}')
    proof = verifier(deepcopy(source), deepcopy(card), deepcopy(observations), deepcopy(evidence or {}))
    require(isinstance(proof, dict) and proof.get('version') == 'complete-source-grade-verification-v1',
            f'Invalid complete-rule proof: {identifier}')
    require(proof.get('indicator_id') == identifier and proof.get('source_sha256') == source['source_sha256']
            and proof.get('rubric_sha256') == source['rubric_sha256'] and proof.get('grade') == selected,
            f'Complete-rule proof binding mismatch: {identifier}')
    for field in ('all_source_conditions_checked', 'unevaluable_gates_passed', 'event_identity_and_stage_verified',
                  'grade_choice_verified', 'complete_source_rule_satisfied'):
        require(proof.get(field) is True, f'Incomplete {field}: {identifier}')
    required_fields = {key: value for key, value in source['source_fields'].items()
                       if key in {'技术定义', '计算方式', '不可评价', '开始标志性动作', '结束标志性动作',
                                  'A级', 'B级', 'C级', 'D级', 'E级'}}
    require({'技术定义', '计算方式', '不可评价', 'A级', 'B级', 'C级', 'D级', 'E级'} <= set(required_fields),
            f'Complete source fields missing: {identifier}')
    require(proof.get('source_fields_checked') == required_fields, f'Incomplete source field audit: {identifier}')
    checked = proof.get('observation_ids')
    require(isinstance(checked, list) and checked and len(set(checked)) == len(checked)
            and set(checked) <= set(card['observation_ids']), f'Invalid complete-rule evidence links: {identifier}')
    for key in checked:
        require(key in (evidence or {}) and bool(evidence[key].get('source_frames')),
                f'Complete rule has no raw frame evidence: {identifier}/{key}')
    require(isinstance(proof.get('verifier_id'), str) and proof['verifier_id']
            and isinstance(proof.get('verifier_version'), str) and proof['verifier_version'],
            f'Complete rule verifier identity missing: {identifier}')
    return deepcopy(proof)


def card_gaps(source, card, observations, complete, verifier_available):
    conditions = []
    for code, text, features, implementation in SUBCONDITIONS.get(source['indicator_id'], []):
        linked = [key for key in card['observation_ids'] if observations[key].get('feature_name') in features
                  or code in observations[key].get('source_condition_codes', [])]
        conditions.append({'code': code, 'source_text': text,
            'source_locator': source['source_fields'].get('计算方式', {}).get('locator'),
            'definition_state': 'explicit_condition_in_source',
            'implementation_state': 'partial_measurement_available' if linked else implementation,
            'observation_ids': linked, 'evidence_state': 'observed_partial' if linked else 'not_observed_in_this_run',
            'complete_rule_satisfied': complete})
    gaps = {'source_definition': deepcopy(source['source_blockers']), 'subcondition_implementation': [],
            'run_evidence': [], 'grade_mapping': []}
    if complete:
        return conditions, gaps
    gaps['subcondition_implementation'] = [{'code': item['code'], 'state': item['implementation_state'],
        'message_zh': '原文已有此子条件；当前缺对应实现或尚未声明可复用的原文绑定。'}
        for item in conditions if not item['observation_ids']]
    recognizer_gap = next((item for item in card['blockers']
                           if item.get('code') == 'event_type_recognizer_not_implemented'), None)
    if recognizer_gap:
        gaps['subcondition_implementation'].append({**deepcopy(recognizer_gap), 'state': 'not_implemented'})
    if not card['observation_count']:
        if recognizer_gap and card['status'] != 'evidence_insufficient':
            gaps['run_evidence'].append({'code': 'event_evidence_not_assessable_without_recognizer',
                'message_zh': '当前缺该动作类型的自动识别实现，尚不能据此判断本视频是否包含该动作及正式阶段。'})
        else:
            missing_event = card['status'] in {'not_observed', 'event_type_unresolved'}
            gaps['run_evidence'].append({'code': 'event_window_missing' if missing_event else 'required_measurement_evidence_missing',
                'message_zh': '本次缺可绑定的同人动作/阶段窗口。' if missing_event else '已有动作候选，但本项连续关节或专用证据不足。'})
    else:
        gaps['run_evidence'].append({'code': 'complete_rule_evidence_unverified',
            'message_zh': '已有部分测量，尚未核验原卡全部条件、同人时序与不可评价门槛。'})
    if source['indicator_id'] == 'FS09-M03':
        gaps['run_evidence'].extend([
            {'code': 'post_braking_joint_sequence_unverified', 'message_zh': '整事件两端25%中位差未证明制动后同一连续子窗内减速、双膝屈曲及髋下降按原文时序发生。'},
            {'code': 'hip_descent_reference_unverified', 'message_zh': '当前髋相对踝高度未排除踝运动；需稳定参照验证髋中心下降。'},
        ])
        gaps['subcondition_implementation'].append({'code': 'complete_inertia_absorption_predicate_not_implemented',
            'state': 'not_implemented', 'message_zh': '原文的明显、连续、协调与无明显失衡完整谓词尚未实现；FS09是制动稳定，回位对应FS10。'})
    if not verifier_available:
        gaps['grade_mapping'].append({'code': 'complete_grade_verifier_not_implemented',
            'message_zh': '完整原卡动作条件、阶段时序及等级判定尚未实现并验证；已有局部测量不能替代，缺人工评分真值也不是此实现缺口的原因。'})
    if any(item.get('code') == 'qualitative_rubric_uncalibrated' for item in card['blockers']):
        gaps['grade_mapping'].append({'code': 'numeric_grade_cutpoints_absent_in_source',
            'message_zh': '原文没有给出测量值到A–E的数值切点；这不否定已有脚序、轨迹或延迟子条件，也不排除验证完整定性判定规则。'})
    return conditions, gaps


def build_matrix(source_cards: list[dict], assessment: dict, *, run_id: str,
                 evidence: dict[str, dict] | None = None, grade_verifiers: dict | None = None) -> dict:
    """Validate one current response, preserving its evidence/grade boundaries."""
    sources = unique_by(source_cards, 'indicator_id', 'source cards')
    require(len(sources) == 298, 'Source catalogue must contain 298 unique cards')
    require(dict(Counter(r['dimension'] for r in sources.values())) == EXPECTED_DIMENSIONS,
            'Source five-dimension grouping differs from audited 70/78/72/25/53')
    require(assessment.get('version') == 'word-indicator-assessment-v1.0.0', 'Unsupported assessment contract')
    declared_graded = assessment.get('counts', {}).get('formal_graded')
    require(isinstance(declared_graded, int) and not isinstance(declared_graded, bool) and 0 <= declared_graded <= len(sources),
            'Invalid formal grade count')
    verifiers = COMPLETE_RULE_VERIFIERS if grade_verifiers is None else grade_verifiers
    indicators = unique_by(assessment.get('indicators', []), 'indicator_id', 'assessment cards')
    require(set(indicators) == set(sources), 'Assessment does not cover the exact 298 source IDs')
    require(assessment.get('counts', {}).get('total') == len(indicators), 'Total card count mismatch')
    observations = unique_by(assessment.get('observations', []), 'observation_id', 'observations')
    require(assessment.get('returned_observation_count') == len(observations), 'Returned observation count mismatch')
    require(assessment.get('counts', {}).get('returned_observations') == len(observations), 'Summary returned count mismatch')
    full = assessment.get('full_observation_count')
    require(isinstance(full, int) and not isinstance(full, bool) and full >= len(observations), 'Invalid full observation count')
    require(assessment.get('counts', {}).get('observations') == full, 'Summary full count mismatch')
    require(assessment.get('is_truncated') is (full > len(observations)), 'Observation sampling flag mismatch')
    for obs in observations.values():
        require(obs.get('status') in {'measured', 'partial'}, f"Unknown observation status: {obs['observation_id']}")
        require(obs.get('value') is None or finite(obs['value']), f"Non-finite measurement: {obs['observation_id']}")
        require(isinstance(obs.get('full_source_rule_satisfied'), bool), 'Invalid complete source-rule flag')
    output = []
    linked_ids = set()
    verified_full_evidence = set()
    for identifier, source in sources.items():
        card = indicators[identifier]
        binding = card.get('source', {})
        require(binding.get('sha256') == source['source_sha256']
                and binding.get('document_id') == source['source_document_id']
                and binding.get('table') == source['source_table']
                and binding.get('rubric_sha256') == source['rubric_sha256'], f'Source/rubric binding mismatch: {identifier}')
        grade = card.get('grade', {})
        require(isinstance(grade, dict), f'Unsupported formal grade: {identifier}')
        graded = grade.get('status') in {'graded', 'source_scored'}
        if not graded:
            require(grade.get('status') in {'calibration_required', 'source_rule_conflict', 'proxy_scored'}
                    and grade.get('value') is None and grade.get('grade') is None, f'Unsupported formal grade: {identifier}')
            if grade.get('status') == 'proxy_scored':
                require(grade.get('semantics') == 'explicit_numeric_proxy_not_complete_source_grade'
                        and isinstance(grade.get('events'), list) and bool(grade['events']),
                        f'Proxy grade semantics or evidence missing: {identifier}')
        linked = card.get('observation_ids', [])
        require(len(linked) == len(set(linked)), f'Duplicate observation link: {identifier}')
        require(all(key in observations for key in linked), f'Unknown observation link: {identifier}')
        accepted = card.get('observation_count')
        require(isinstance(accepted, int) and not isinstance(accepted, bool) and accepted >= len(linked), f'Invalid accepted observation count: {identifier}')
        require(card.get('returned_observation_count') == len(linked), f'Card display count mismatch: {identifier}')
        require(card.get('observations_truncated') is (accepted > len(linked)), f'Card truncation mismatch: {identifier}')
        blockers = card.get('blockers', [])
        required = {b['code'] for b in source['source_blockers']}
        require(required <= {b['code'] for b in blockers}, f'Source blockers lost: {identifier}')
        hard_blocked = bool(required & SOURCE_BLOCKERS)
        if hard_blocked:
            require(card['status'] in required, f'Source-blocked status overwritten: {identifier}')
        proof = verify_complete_grade(source, card, observations, evidence, verifiers.get(identifier)) if graded else None
        if proof:
            verified_full_evidence.update(proof['observation_ids'])
        state = 'implemented' if proof else 'blocked' if hard_blocked or not accepted else 'measurement_only'
        if accepted:
            require(bool(linked), f'Measured card has no retained representative evidence: {identifier}')
        linked_ids.update(linked)
        conditions, gaps = card_gaps(source, card, observations, bool(proof), callable(verifiers.get(identifier)))
        output.append({
            'indicator_id': identifier, 'dimension': source['dimension'], 'event_code': source['event_code'],
            'stage_code': source['stage_code'], 'name_zh': source['name_zh'], 'acceptance_state': state,
            'assessment_status': card['status'], 'reason_zh': card['explanation_zh'],
            'source': {'document_id': source['source_document_id'], 'sha256': source['source_sha256'],
                       'table': source['source_table'], 'rubric_sha256': source['rubric_sha256'],
                       'original_fields': source['source_fields']},
            'source_calculation': source['source_calculation'], 'source_unevaluable': source['source_unevaluable'],
            'source_human_qualitative_grade_allowed': source['source_allows_human_qualitative_grade'],
            'grade_state': deepcopy(grade), 'complete_source_grade_verified': bool(proof),
            'complete_rule_verification': proof, 'source_subconditions': conditions, 'gap_layers': gaps,
            'blockers': deepcopy(blockers), 'source_warnings': source['source_warnings'],
            'accepted_linked_observation_count': accepted, 'returned_linked_observation_count': len(linked),
            'observations_truncated': card['observations_truncated'], 'observation_ids': list(linked),
            'measurement_sources': sorted({observations[key]['source'] for key in linked}),
            'formula_and_evidence_ids': list(linked),
            'prerequisites': ['完整原卡动作/阶段与同人事件证据', '与原文匹配的可观测量及误差验证',
                              '完整可执行且经验证的等级规则；原文定性描述不能换成任意数值切点']
                             + [b.get('message_zh', b.get('message', b['code'])) for b in blockers],
            'checks': {'source_binding_matches': True, 'source_blockers_preserved': True,
                       'all_returned_evidence_resolves': True, 'no_invented_grade': True},
        })
    require(sum(row['complete_source_grade_verified'] for row in output) == declared_graded, 'Formal grade count mismatch')
    require({key for key, obs in observations.items() if obs['full_source_rule_satisfied']} <= verified_full_evidence,
            'Complete source-rule claim has no independently verified grade evidence')
    dimension_counts = {}
    for dimension in EXPECTED_DIMENSIONS:
        selected = [r for r in output if r['dimension'] == dimension]
        counts = Counter(r['acceptance_state'] for r in selected)
        dimension_counts[dimension] = {'total': len(selected), **{state: counts[state] for state in ('implemented', 'measurement_only', 'blocked')},
            'cards_with_any_accepted_measurement': sum(r['accepted_linked_observation_count'] > 0 for r in selected)}
    counts = Counter(r['acceptance_state'] for r in output)
    measured_ids = {r['indicator_id'] for r in output if r['accepted_linked_observation_count'] > 0}
    source_measured = {r['indicator_id'] for r in output if 'source_aligned' in r['measurement_sources']}
    legacy_measured = {r['indicator_id'] for r in output if 'legacy_feature' in r['measurement_sources']}
    contextual_measured = {r['indicator_id'] for r in output if 'motion_joint_measurement' in r['measurement_sources']}
    enriched = []
    for key, obs in observations.items():
        extra = (evidence or {}).get(key, {})
        enriched.append({**deepcopy(obs), 'audit_evidence': deepcopy(extra),
                         'linked_source_card_count': sum(key in r['observation_ids'] for r in output)})
    return {'version': 'indicator-acceptance-matrix-v1', 'run_id': run_id,
            'semantics': 'source_card_and_measurement_evidence_acceptance_not_recognition_or_grade_accuracy',
            'ground_truth_used': False, 'gpu_inference_performed': False,
            'acceptance_definition': {'implemented': '可信逐卡验证器已复核完整原卡规则、等级选择和本次原始证据；不等于准确率验证',
                'measurement_only': '本次有原文局部/历史代理/相关动作实测，但完整等级未验证',
                'blocked': '原文冲突/阶段边界缺失，或本次无可绑定测量；不等于零分/E级/检测不准确'},
            'states': {state: counts[state] for state in ('implemented', 'measurement_only', 'blocked')},
            'dimensions': dimension_counts, 'cards': output, 'observations': enriched,
            'coverage': {
                'catalogue_response': ratio(len(output), len(sources), '返回原文卡数 / 298；不是可评分覆盖率'),
                'complete_source_grade': ratio(counts['implemented'], len(sources), '已验证完整原卡等级 / 298'),
                'cards_with_any_measurement': ratio(len(measured_ids), len(sources), '有任一接受测量的唯一原卡 / 298；含相关测量且不等于全部条件满足'),
                'source_aligned_partial_cards': ratio(len(source_measured), len(sources), '有原文局部测量的唯一卡 / 298；与其他来源可重叠'),
                'legacy_proxy_cards': ratio(len(legacy_measured), len(sources), '有历史代理测量的唯一卡 / 298；与其他来源可重叠'),
                'contextual_cards': ratio(len(contextual_measured), len(sources), '有相关动作测量的唯一卡 / 298；不等同正式GS阶段'),
                'returned_observations': ratio(len(observations), full, '返回代表观测数 / 接受观测总数；独立观测ID去重，不重复计算多卡链接'),
                'card_linked_returned_observations': ratio(len(linked_ids), len(observations), '至少关联一张原卡的返回观测 / 全部返回观测；未绑定动作类型的观测保留但不填入卡'),
                'recognition_accuracy': {'value': None, 'reason': '无独立标注的检测/事件真值'},
                'technique_grade_accuracy': {'value': None, 'reason': '无教练等级真值；几何/单测不能替代'},
            }, 'observation_selection': assessment.get('observation_selection'),
            'limitations_zh': ['同一观测可以关联多卡，来源子计数不能相加成通过数。',
                '界面代表样本的帧证据已核对存在性，不等于关节/身份/事件语义准确性已标定。',
                '部分上下文观测可能尚无正式技术类型，因此不能用于对应GS卡。',
                '完整原始观测保存在输入产物；此矩阵保留全部298卡和全部返回代表观测，不伪造未返回样本。']}


def load_run_evidence(run_dir: Path, assessment: dict, effective_summary: dict,
                      source_measurements_path: Path | None = None) -> tuple[dict, dict]:
    """Attach explicit raw feature/frame references, never interpolate a gap."""
    from rallymate_features.event_features import FEATURE_DEFINITIONS
    from rallymate_scoring.indicator_observations import MEASUREMENTS
    events = unique_by(rows(run_dir / 'events.jsonl'), 'event_id', 'raw events')
    legacy = {}
    for record in rows(run_dir / 'indicator-features.jsonl'):
        for feature in record.get('features', []):
            key = (record['indicator_id'], record['event_id'], feature['feature_name'])
            require(key not in legacy, f'Duplicate raw event feature: {key}')
            legacy[key] = (record, feature)
    aligned = {}
    source_path = source_measurements_path or run_dir / 'source-aligned-measurements.json'
    if source_path.exists():
        for record in read(source_path)['records']:
            for item in record['indicators']:
                for measurement in item['source_measurements']:
                    key = (item['indicator_id'], record['event_id'], measurement['feature_name'])
                    require(key not in aligned, f'Duplicate raw source feature: {key}')
                    aligned[key] = (record, item, measurement)
    frames = {}
    with (run_dir / 'frames.jsonl').open(encoding='utf-8') as stream:
        for line in stream:
            if not line.strip(): continue
            raw = json.loads(line)
            frame = raw['frame']
            index, timestamp = frame['index'], frame['timestamp_ms']
            require(index not in frames and finite(timestamp), f'Invalid frame identity/time: {index}')
            frames[index] = {'frame_index': index, 'timestamp_ms': timestamp,
                             'timestamp_source': frame.get('timestamp_source', 'unspecified_in_historical_artifact')}
    episodes = {}
    families = effective_summary.get('action_recognition', {}).get('motion_analysis', {}).get('families', {})
    for family in families.values():
        for episode in family.get('episodes', []):
            episodes.setdefault(episode.get('episode_id'), episode)
    evidence = {}
    for observation in assessment['observations']:
        id = observation['observation_id']
        key = (observation.get('indicator_id'), observation.get('event_id'), observation['feature_name'])
        feature_name, source = observation['feature_name'], observation['source']
        frame_ids = list(observation.get('source_frames', []))
        detail = {'formula_origin': 'explicit_engineering_measurement_not_Word_grade',
                  'formula': observation.get('definition_zh'), 'unit': observation['unit'],
                  'event_or_episode_id': observation.get('event_id') or observation.get('episode_id'),
                  'identity_accuracy_verified': False, 'event_semantics_verified': False}
        if source == 'legacy_feature':
            require(key in legacy, f'Observation has no raw legacy feature: {id}')
            record, feature = legacy[key]
            require(feature.get('valid') is True and feature['value'] == observation['value']
                    and feature['unit'] == observation['unit'], f'Legacy value/unit mismatch: {id}')
            require(frame_ids == feature['source_frames'], f'Legacy source frames mismatch: {id}')
            detail.update(raw_artifact='indicator-features.jsonl', raw_value_verified=True,
                          formula=FEATURE_DEFINITIONS.get(feature_name, {}).get('definition', detail['formula']),
                          formula_metadata=FEATURE_DEFINITIONS.get(feature_name),
                          quality_gate=record.get('quality_gate'), person_track_id=record.get('person_track_id'),
                          measurement_provenance=record.get('provenance'))
        elif source == 'source_aligned':
            require(key in aligned, f'Observation has no raw source-aligned feature: {id}')
            record, item, feature = aligned[key]
            require(feature.get('status') == 'measured' and feature['value'] == observation['value']
                    and feature['unit'] == observation['unit'], f'Source-aligned value/unit mismatch: {id}')
            frame_ids = list(feature.get('source_frames', []))
            detail.update(raw_artifact=str(source_path), raw_value_verified=True,
                          formula=feature.get('definition'), quality_gate=item.get('source_rule_gate'),
                          measurement_evidence=feature.get('evidence'), event_association=item.get('event_association'),
                          person_track_id=record.get('person_track_id'))
        elif source == 'motion_joint_measurement':
            episode = episodes.get(observation.get('episode_id'))
            require(episode is not None, f'Contextual episode missing: {id}')
            require(episode['start_ms'] <= observation['start_ms'] < observation['end_ms'] <= episode['end_ms'],
                    f'Contextual measurement outside episode: {id}')
            require(episode['person_track_id'] == observation['person_track_id'], f'Contextual track mismatch: {id}')
            for window in observation.get('windows', []):
                declared = window.get('source_frames', [])
                require(len(declared) == len(set(declared)), f'Duplicate contextual frame: {id}')
                if declared:
                    require(window.get('sample_count') == len(declared), f'Contextual frame/sample count mismatch: {id}')
                frame_ids.extend(declared)
            frame_ids = sorted(set(frame_ids))
            spec = MEASUREMENTS.get(feature_name)
            detail.update(raw_artifact='effective action_recognition replay', raw_value_verified=None,
                          formula=spec[3] if spec else detail['formula'], required_joints=list(spec[0]) if spec else None,
                          source_windows=observation.get('windows'), coverage=observation.get('coverage'),
                          person_track_id=observation.get('person_track_id'),
                          value_validation_scope='exported window arithmetic/series source, not independent geometry recomputation')
        else:
            raise MatrixAuditError(f'Unsupported measurement source: {source}')
        require(all(isinstance(f, int) and not isinstance(f, bool) and f in frames for f in frame_ids), f'Out-of-artifact source frame: {id}')
        # Legacy aggregates can encode [first, last] with the same frame twice
        # for a one-sample run. Preserve those references, but count that frame
        # only once; do not turn endpoint references into independent samples.
        detail['raw_source_frame_references'] = list(frame_ids)
        detail['repeated_endpoint_reference_count'] = len(frame_ids) - len(set(frame_ids))
        frame_ids = list(dict.fromkeys(frame_ids))
        resolved = [frames[f] for f in frame_ids]
        event = events.get(observation.get('event_id'))
        if observation.get('event_id'):
            require(event is not None, f'Missing source event: {id}')
            detail['event'] = {k: event.get(k) for k in ('event_id', 'event_code', 'person_track_id', 'start_ms', 'end_ms', 'key_phases_ms', 'quality_flags')}
        detail['source_frames'] = resolved
        detail['frame_evidence_state'] = 'explicit_frame_ids_found_in_raw_artifact' if resolved else 'time_window_only'
        start = observation.get('start_ms')
        end = observation.get('end_ms')
        if start is None and resolved:
            start, end = min(f['timestamp_ms'] for f in resolved), max(f['timestamp_ms'] for f in resolved)
        detail['replay'] = {'job_id': run_dir.name, 'start_ms': start, 'end_ms': end,
                            'time_source': 'measurement_window' if observation.get('start_ms') is not None else 'explicit_source_frame_extent',
                            'interpolation_used': False}
        evidence[id] = detail
    return evidence, {'frame_count': len(frames), 'raw_event_count': len(events),
                     'explicit_frame_evidence_observations': sum(bool(e['source_frames']) for e in evidence.values()),
                     'time_window_only_observations': sum(not e['source_frames'] for e in evidence.values()),
                     'observations_with_repeated_endpoint_references': sum(e['repeated_endpoint_reference_count'] > 0 for e in evidence.values())}


def preparation_gap_audit(source_cards, assessment, process_report, raw_summary, run_dir):
    """Audit all 78 preparation cards; generic candidates never become GS links."""
    from rallymate_scoring.indicator_observations import MEASUREMENTS
    from rallymate_service.preparation_observations import preparation_candidate_observations, preparation_translation_observations
    from rallymate_service.process_evaluation import _episodes
    sources = {row['indicator_id']: row for row in source_cards if row['dimension'] == 'preparation'}
    require(len(sources) == 78, 'Preparation audit requires all 78 source cards')
    dimension = next((row for row in process_report.get('dimensions', []) if row.get('id') == 'preparation'), None)
    require(dimension is not None, 'Process report has no preparation dimension')
    displayed = [row for row in dimension['criteria'] if row['id'].startswith(('preparation_candidate_', 'preparation_translation_'))]
    episodes = _episodes(raw_summary)
    full = preparation_candidate_observations(episodes) + preparation_translation_observations(episodes)
    key = lambda row: (row['id'], row['episode_id'], row['start_ms'], row['end_ms'])
    available = {key(row): row for row in full}
    require(len(available) == len(full), 'Duplicate full preparation projection')
    require(all(key(row) in available and row.get('video_id') in {None, run_dir.name}
                and {name: value for name, value in row.items() if name != 'video_id'} == available[key(row)] for row in displayed),
            'Process preparation projection differs from raw summary metrics')
    frames = {row['frame']['index']: row['frame']['timestamp_ms'] for row in rows(run_dir / 'frames.jsonl')}
    for row in full:
        require(row.get('indicator_id') is None and row.get('source_card_applicability') == 'unconfirmed'
                and row.get('full_source_rule_satisfied') is False and row.get('score') is None,
                'Generic preparation evidence promoted to a formal source card')
        require(row['sample_count'] == len(row['source_frames']) == len(row['source_timestamps_ms']), 'Projection frame count mismatch')
        # Bound model PTS may differ from the video's playback clock. Validate
        # replay with video timestamps; the producer verifies source PTS binding.
        video_times = row.get('video_timestamps_ms', row['source_timestamps_ms'])
        require(len(video_times) == len(row['source_frames']) and
                all(frames.get(frame) == timestamp for frame, timestamp in zip(row['source_frames'], video_times)),
                'Preparation projection frame/time mismatch with raw video artifacts')
        allowed_anchors = set()
        for identifier in row['related_source_cards']:
            require(identifier in sources, 'Projection refers to an unknown preparation card')
            anchor = sources[identifier]['source_fields']['计算方式']
            anchors = anchor.get('locators', [anchor.get('locator')])
            require(bool(set(row['source_refs']) & set(anchors)), 'Preparation projection has an incorrect Word anchor')
            allowed_anchors.update(anchors)
        require(set(row['source_refs']) <= allowed_anchors, 'Preparation projection has an incorrect Word anchor')
    by_feature = Counter(row['feature_name'] for row in full)
    generic_joint = [row for row in assessment['observations'] if row.get('phase') == 'preparation'
                     and row['source'] == 'motion_joint_measurement']
    joint_counts = Counter(row['feature_name'] for row in generic_joint)
    indicators = {row['indicator_id']: row for row in assessment['indicators']}
    cards = []
    for identifier, source in sources.items():
        fields, calculation = source['source_fields'], source['source_calculation']
        rule_blockers = {row['code'] for row in source['source_blockers']}
        reusable, missing_algorithms, predicates = [], [], []

        def reuse(feature, scope, formula=None):
            spec = MEASUREMENTS.get(feature)
            reusable.append({'feature_name': feature, 'scope': scope,
                'formula': formula or (spec[3] if spec else None),
                'required_joints': list(spec[0]) if spec else None,
                'producer': 'rallymate_service/preparation_observations.py' if feature in by_feature else 'rallymate_scoring/indicator_observations.py',
                'full_projection_observation_count': by_feature.get(feature, 0),
                'returned_joint_observation_count': joint_counts.get(feature, 0),
                'formal_source_card_binding': 'unconfirmed', 'source_calculation_field': deepcopy(fields['计算方式'])})

        if '肩线/髋线' in calculation:
            for feature in ('shoulder_axis_deg', 'hip_axis_deg'):
                reuse(feature, 'source_requested_2d_axis_measurement')
            for feature in ('shoulder_line_change_deg', 'hip_line_change_deg', 'peak_shoulder_angular_speed_deg_s', 'peak_hip_angular_speed_deg_s'):
                example = next((row for row in full if row['feature_name'] == feature), None)
                reuse(feature, 'source_requested_2d_rotation_measurement', example['rule_zh'] if example else '按实测时间求二维轴角变化与角速度；需连续同轴有效窗口')
            predicates.append({'condition': '二维肩/髋轴角是否变化及方向/先后关系',
                               'executable_part': '在同一有效阶段窗上比较实测轴角及角速度；不引入任意技术幅度切点。'})
        if '腕/肘轨迹' in calculation:
            for feature in ('net_displacement_torso', 'path_length_torso', 'mean_speed_torso_per_s', 'start_speed_torso_per_s', 'end_speed_torso_per_s'):
                example = next((row for row in full if row['feature_name'] == feature), None)
                reuse(feature, 'partial_single_selected_wrist_not_both_arms', example['rule_zh'] if example else '连续二维腕轨迹的位移/速度')
            reuse('wrist_separation_torso', 'partial_bilateral_wrist_distance_not_racket_grip')
            missing_algorithms.append('双肘轨迹与速度、腕肘跨侧相对距离尚未作为本卡完整子条件接通；肘角不能替代肘轨迹。')
            predicates.append({'condition': '腕部位移、路径、起止速度与双腕相对距离',
                               'executable_part': '已有数值计算可直接保留；确认持拍/非持拍侧和原文窗后才能评价相应动作关系。'})
        if '膝踝角度' in calculation:
            for feature in ('left_knee_flexion_deg', 'right_knee_flexion_deg'):
                reuse(feature, 'partial_knee_angle_not_ankle_angle_or_force')
            for feature in ('left_knee_flexion_change_deg', 'right_knee_flexion_change_deg'):
                example = next((row for row in full if row['feature_name'] == feature), None)
                if example:
                    reuse(feature, 'partial_knee_change_in_local_window_not_ankle_or_force', example['rule_zh'])
            missing_algorithms.append('踝关节角度/足部接触事件尚未按本卡接通；不能从髋—膝—踝膝角推导踝角或承重。')
        if '髋中心位移/速度' in calculation:
            reuse('hip_to_ankle_midpoint_torso', 'related_hip_ankle_offset_not_hip_translation')
            reuse('hip_inside_ankle_support_fraction', 'related_projected_support_not_actual_weight_transfer')
            translation = ('hip_center_dx_torso', 'hip_center_dy_torso', 'hip_center_mean_velocity_x_torso_per_s', 'hip_center_mean_velocity_y_torso_per_s')
            for feature in translation:
                example = next((row for row in full if row['feature_name'] == feature), None)
                if example:
                    reuse(feature, 'stable_camera_local_window_translation_not_formal_stage_or_weight_transfer', example['rule_zh'])
            if any(by_feature.get(feature) for feature in translation):
                missing_algorithms.append('已有稳定机位参照、固定窗身体尺度的髋中心局部位移/速度；仍缺正式阶段及持拍侧关联，局部窗不能证明整阶段加载/承重或落地时序。')
                predicates.append({'condition': '局部窗内髋中点的有符号位移、平均速度及双膝角变化',
                    'executable_part': '保留实测画面右/下为正的位移与真实时间差速度；在独立确认原卡起止后可检验方向、变化及先后关系。',
                    'missing_inputs': ['原卡完整阶段窗', '持拍侧与人体身份关联', '视角/竖直参照', '承重需独立支撑证据，不能由髋中点替代']})
            else:
                missing_algorithms.append('本卡所需髋中心位移/速度的稳定参照阶段轨迹尚未接通；身体归一化髋踝偏移不能替代真实重心转移。')
        if '球拍关键点' in calculation:
            missing_algorithms.append('球拍拍头/拍柄/拍轴/拍面专用关键点及同人球拍关联缺失；球拍框和腕轨迹不能替代。')
        if '球轨迹' in calculation:
            missing_algorithms.append('需独立活动球轨迹与本次身体/球拍时空绑定；腕速峰值不能替代触球或来球。')
        if '人体关键点时序变化' in calculation:
            missing_algorithms.append('需针对原行为实现时序判定；已有通用关节窗口不能直接认定垫步、启动或持续盯球。')
        if '盯球' in source['name_zh']:
            missing_algorithms.append('真实持续注视尚无可信输入；鼻部/头部位置只能描述几何，不能判定眼球注视。')
        if '拍头高于预计击球点' in source['name_zh']:
            predicates.append({'condition': '拍头高于预计击球点',
                'executable_part': '在共同标定竖直参照中检验拍头与独立预计击球点的有符号高度差；原文已有高于关系，无需先发明A–E数值切点。',
                'missing_inputs': ['可信拍头关键点', '独立球轨迹/预计击球点', '同机位竖直参照', '本技术正式M05窗口']})
        related = [row for row in full if identifier in row['related_source_cards']]
        source_stage_missing = 'source_boundaries_missing' in rule_blockers
        card = indicators[identifier]
        recognizer_gap = next((item for item in card['blockers']
                               if item.get('code') == 'event_type_recognizer_not_implemented'), None)
        if recognizer_gap:
            missing_algorithms.append(recognizer_gap['message_zh'])
        cards.append({'indicator_id': identifier, 'name_zh': source['name_zh'], 'event_code': source['event_code'],
            'stage_code': source['stage_code'], 'source_document_sha256': source['source_sha256'],
            'source_table': source['source_table'], 'rubric_sha256': source['rubric_sha256'],
            'source_calculation': deepcopy(fields['计算方式']), 'source_required_points': deepcopy(fields.get('所需点')),
            'source_unevaluable': deepcopy(fields['不可评价']),
            'stage_window_required': {'start': deepcopy(fields.get('开始标志性动作')), 'end': deepcopy(fields.get('结束标志性动作')),
                'state': 'source_definition_missing' if source_stage_missing else 'source_defined_detector_not_implemented',
                'current_estimated_preparation_is_formal_stage': False},
            'source_definition_blockers': deepcopy(source['source_blockers']),
            'source_warnings': deepcopy(source['source_warnings']),
            'reusable_algorithm_candidates': reusable, 'directly_operable_subconditions': predicates,
            'unimplemented_or_unconnected_algorithms': missing_algorithms,
            'current_run_evidence': {'formal_card_observation_count': card['observation_count'],
                'explicitly_related_generic_projection_count': len(related),
                'explicitly_related_generic_projection_ids': [f"{row['episode_id']}:{row['id']}:{row['start_ms']}:{row['end_ms']}" for row in related],
                'related_is_formal_applicability': False,
                'missing': ['独立持拍手/球拍关联',
                            f"{source['event_code']}自动类型识别尚未实现，当前不能核验" if recognizer_gap else f"可信{source['event_code']}技术类型",
                            f"{source['stage_code']}原文阶段起止",
                            '原文要求关键点的有效帧比例与身份连续性在正式窗内的核验']},
            'source_grade_conditions': {level: deepcopy(fields[level + '级']) for level in 'ABCDE'},
            'grade_rule_gap': {'qualitative_conditions_present': True,
                'numeric_grade_cutpoints_present': bool(source.get('source_numeric_grade_texts')),
                'complete_grade_verifier_implemented': callable(COMPLETE_RULE_VERIFIERS.get(identifier)),
                'reason_zh': '原文已有定性等级描述；当前缺完整可执行判定与证据绑定。无数值切点不等于无动作子条件，也不以缺人工真值阻止几何测量。'},
            'ground_truth_role': '独立真值用于验证识别/等级准确率；不是计算原文已有几何量的前置条件'})
    return {'version': 'preparation-78-source-gap-audit-v1', 'run_id': run_dir.name,
        'card_count': len(cards), 'cards': cards,
        'generic_preparation_projection': {'full_count_from_raw_summary': len(full), 'returned_process_count': len(displayed),
            'candidate_projection_count': sum(row['id'].startswith('preparation_candidate_') for row in full),
            'translation_projection_count': sum(row['id'].startswith('preparation_translation_') for row in full),
            'translation_full_phase_coverage_count': sum(row['id'].startswith('preparation_translation_') and row.get('full_phase_coverage') is True for row in full),
            'process_preparation_full_measurement_count': dimension['measured_criterion_count'],
            'process_preparation_returned_criteria_count': len(dimension['criteria']),
            'full_feature_counts': dict(by_feature), 'observations': full,
            'source_frame_timestamps_verified': True, 'word_calculation_anchors_verified': True,
            'source_card_applicability': 'unconfirmed', 'formal_card_coverage_added': 0},
        'summary': {'source_conflict_cards': sum(any(b['code'] == 'source_rule_conflict' for b in row['source_definition_blockers']) for row in cards),
            'source_missing_stage_boundary_cards': sum(row['stage_window_required']['state'] == 'source_definition_missing' for row in cards),
            'cards_with_reusable_algorithm_candidates': sum(bool(row['reusable_algorithm_candidates']) for row in cards),
            'cards_with_explicit_related_generic_projection': sum(row['current_run_evidence']['explicitly_related_generic_projection_count'] > 0 for row in cards),
            'unclassified_episode_count': sum(row.get('classification', {}).get('label') == 'unclassified' for row in episodes)},
        'accuracy': {'value': None, 'reason': '未使用独立检测、阶段或教练等级真值'},
        'limitations': ['复核现有原始指标与帧时间绑定；没有重新运行GPU，也未独立重算全部原始几何。',
                        '算法候选关联是原文需求可复用清单，不能计作对应技术卡已观测或完整评分通过。']}


def load_action_replay(action_path, run_dir):
    """Keep a new CPU replay bound to immutable original decoder observations."""
    proof_path = action_path.parent / 'evidence.json'
    source_hashes = read(proof_path).get('source_hashes', {})
    require(isinstance(source_hashes, dict) and bool(source_hashes), 'Action replay lacks source input hashes')
    resolved = {(ROOT / relative).resolve(): expected for relative, expected in source_hashes.items()}
    require((run_dir / 'frames.jsonl').resolve() in resolved, 'Action replay lacks frame-artifact binding')
    for path, expected in resolved.items():
        require(path.is_relative_to(run_dir.resolve()) and digest(path) == expected,
                'Action replay input does not bind selected run')
    hashes = {str(path): digest(path) for path in (*resolved, action_path.resolve(), proof_path.resolve())}
    return read(action_path), hashes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-matrix', type=Path, default=QA / 'scoring-source-audit/per-card-source-matrix.json')
    parser.add_argument('--replay-dir', type=Path, default=QA / 'qa-evidence/scoring-measurement-replay')
    parser.add_argument('--runs-root', type=Path, default=ROOT / 'service_data/runs')
    parser.add_argument('--output-dir', type=Path, default=QA / 'qa-evidence/indicator-acceptance-matrix')
    parser.add_argument('--run-id', action='append')
    parser.add_argument('--process-report', action='append', default=[], metavar='RUN_ID=PATH',
                        help='Attach a saved process report for preparation source-gap audit; never upgrades generic GS applicability')
    parser.add_argument('--source-assessment', action='append', default=[], metavar='RUN_ID=PATH',
                        help='Compose an audited source measurement replay with the current effective action summary')
    parser.add_argument('--action-recognition', action='append', default=[], metavar='RUN_ID=PATH',
                        help='Use an explicit CPU motion replay, binding its companion evidence.json source_hashes to the original run')
    args = parser.parse_args()
    output = args.output_dir.resolve()
    require(not output.is_relative_to((ROOT / 'reports').resolve())
            and not output.is_relative_to(args.runs_root.resolve())
            and output != args.replay_dir.resolve(), 'Output must not overwrite historical/raw/replay inputs')
    source_cards = read(args.source_matrix)
    process_reports = {}
    for entry in args.process_report:
        run_id, separator, path = entry.partition('=')
        require(separator and run_id and path and run_id not in process_reports, 'Invalid duplicate/missing process-report binding')
        process_reports[run_id] = Path(path)
    source_assessments = {}
    for entry in args.source_assessment:
        run_id, separator, path = entry.partition('=')
        require(separator and run_id and path and run_id not in source_assessments, 'Invalid source-assessment binding')
        source_assessments[run_id] = Path(path)
    action_replays = {}
    for entry in args.action_recognition:
        run_id, separator, path = entry.partition('=')
        require(separator and run_id and path and run_id not in action_replays, 'Invalid action-recognition binding')
        action_replays[run_id] = Path(path)
    manifest = read(args.replay_dir / 'summary.json')
    selected = [r for r in manifest['runs'] if not args.run_id or r['run_id'] in args.run_id]
    require(bool(selected), 'No replay runs selected')
    require(set(process_reports) <= {row['run_id'] for row in selected}, 'Process report has no selected run')
    require(set(source_assessments) <= {row['run_id'] for row in selected}, 'Source assessment has no selected run')
    require(set(action_replays) <= {row['run_id'] for row in selected}, 'Action replay has no selected run')
    if args.run_id:
        require(set(args.run_id) == {r['run_id'] for r in selected}, 'Unknown requested run ID')
    output.mkdir(parents=True, exist_ok=True)
    results = []
    for replay in selected:
        run_id = replay['run_id']
        require(Path(run_id).name == run_id and '/' not in run_id and '\\' not in run_id, 'Invalid run ID')
        run_dir = args.runs_root / run_id
        paths = [run_dir / name for name in ('summary.json', 'events.jsonl', 'indicator-features.jsonl', 'frames.jsonl', 'source-aligned-measurements.json')]
        hashes = {str(p.resolve()): digest(p) for p in paths if p.exists()}
        for relative, expected in replay['input_sha256'].items():
            path = ROOT / relative
            require(digest(path) == expected, f'Replay input changed: {relative}')
        summary = read(run_dir / 'summary.json')
        raw_summary = deepcopy(summary)
        for relative in replay['input_sha256']:
            if relative.endswith('-after-source-measurements.json'):
                summary['action_recognition'] = read(ROOT / relative)
        if run_id in action_replays:
            action_path = action_replays[run_id]
            summary['action_recognition'], action_hashes = load_action_replay(action_path, run_dir)
            hashes.update(action_hashes)
            raw_summary['action_recognition'] = deepcopy(summary['action_recognition'])
        assessment_path = args.replay_dir / f'{run_id}-indicator-assessment.json'
        assessment = read(assessment_path)
        source_path = None
        if run_id in source_assessments or run_id in action_replays:
            from rallymate_scoring.indicator_assessment import build_indicator_assessment
            if run_id in source_assessments:
                replacement = source_assessments[run_id]
                source_path = replacement.parent / 'source-aligned-measurements.json'
                for relative, expected in read(replacement.parent / 'evidence.json')['input_sha256'].items():
                    require(digest(ROOT / relative) == expected, f'Source measurement replay input changed: {relative}')
                hashes.update({str(path.resolve()): digest(path) for path in (replacement, source_path)})
                source_assessment = read(replacement)
            else:
                source_assessment = summary.get('source_aligned_assessment')
            summary['job_id'] = run_id
            assessment = build_indicator_assessment(summary, rows(run_dir / 'indicator-features.jsonl'), source_assessment=source_assessment)
            (output / f'{run_id}-composed-indicator-assessment.json').write_text(
                json.dumps(assessment, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
        evidence, evidence_counts = load_run_evidence(run_dir, assessment, summary, source_measurements_path=source_path)
        matrix = build_matrix(source_cards, assessment, run_id=run_id, evidence=evidence)
        matrix['process_dimensions'] = deepcopy(replay.get('after'))
        matrix['process_dimension_origins'] = {row['id']: 'saved_scoring_measurement_replay'
                                               for row in matrix['process_dimensions'] or []}
        if run_id in process_reports:
            process_path = process_reports[run_id]
            process_report = read(process_path)
            preparation = preparation_gap_audit(source_cards, assessment, process_report, raw_summary, run_dir)
            matrix['preparation_source_gap_audit'] = preparation
            # This optional report was provided to audit preparation. Other
            # dimensions can omit footwork inputs, so do not overwrite them.
            current_preparation = next(row for row in process_report['dimensions'] if row['id'] == 'preparation')
            for row in matrix['process_dimensions'] or []:
                if row['id'] == 'preparation':
                    row.update({key: current_preparation.get(key) for key in row})
                    row['returned_criteria_count'] = len(current_preparation['criteria'])
                    matrix['process_dimension_origins']['preparation'] = str(process_path.resolve())
            (output / f'{run_id}-preparation-78-gap-audit.json').write_text(
                json.dumps(preparation, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
        matrix['evidence_counts'] = evidence_counts
        matrix['input_sha256'] = {**hashes, str(assessment_path.resolve()): digest(assessment_path),
                                  str(args.source_matrix.resolve()): digest(args.source_matrix)}
        if run_id in process_reports:
            matrix['input_sha256'][str(process_reports[run_id].resolve())] = digest(process_reports[run_id])
        matrix['preserved_engineering_reference_scores'] = replay.get('existing_dimension_scores_unchanged')
        matrix['video_binding'] = summary.get('minimum_scoring_loop', {}).get('provenance', {})
        require(all(digest(Path(p)) == h for p, h in hashes.items()), 'Raw input changed during matrix build')
        destination = output / f'{run_id}-acceptance-matrix.json'
        destination.write_text(json.dumps(matrix, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
        result = {'run_id': run_id, 'states': matrix['states'], 'coverage': matrix['coverage'],
                  'dimensions': matrix['dimensions'], 'evidence_counts': evidence_counts,
                  'raw_inputs_unchanged': True, 'matrix_path': str(destination), 'matrix_sha256': digest(destination)}
        results.append(result)
        print(json.dumps({'run_id': run_id, 'states': matrix['states'], 'evidence_counts': evidence_counts}, ensure_ascii=False))
    complete_coverage = all(run['states']['implemented'] == 298 for run in results)
    result = {'version': 'indicator-acceptance-matrix-portfolio-v1', 'runs': results,
              'source_matrix_sha256': digest(args.source_matrix), 'builder_sha256': digest(Path(__file__)),
              'ground_truth_used': False, 'gpu_inference_performed': False,
              'acceptance_passed': complete_coverage, 'acceptance_scope': 'complete_source_rule_coverage_not_accuracy_or_release',
              'release_blocker': '仍需独立人工真值验证准确性；完整规则覆盖不等于上线验收' if complete_coverage
                                 else '完整298卡技术等级尚不可验证；矩阵构造与证据一致性通过不等于业务全部通过'}
    (output / 'summary.json').write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
