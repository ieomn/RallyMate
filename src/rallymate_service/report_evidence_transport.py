"""Lossless, explicitly negotiated model-proof pooling for report transport.

This is neither a measurement producer nor a scoring gate. Native reports and
saved artifacts stay unchanged; repeated complete proofs use local pool indexes.
"""
from __future__ import annotations

import json
import math
from typing import Any, Literal

from rallymate_scoring.model_measurement_contract import (
    MODEL_MEASUREMENT_PROVENANCE, SOURCE_BINDING_FIELDS,
    validated_geometry_mask, validated_model_window,
)

ReportTransport = Literal['native', 'model-proof-pool-v1']
MODEL_PROOF_CODEC = 'model-proof-pool-v1'
MAX_MODEL_PROOFS = 2048
MAX_MODEL_PROOF_REFERENCES = 20000
# Full-length reports reuse validated proofs; this is the conservative expanded
# JSON bound, not the pooled wire size (delivery remains capped at 128 MiB).
MAX_EXPANDED_REPORT_BYTES = 512 * 1024 * 1024
MAX_DEPTH = 32
MAX_BASE_NODES = 2_000_000
CODEC, POOL, REF, TRANSPORT = 'model_evidence_codec', 'model_evidence_pool', 'model_evidence_ref', 'report_transport'
PROOF_KEYS = ('measurement_provenance', 'source_window', 'geometry_mask', 'source_track_id')
WINDOW_KEYS = ('start_ms', 'end_ms', 'source_frames', 'video_timestamps_ms', 'source_timestamps_ms',
               'source_bindings', 'person_track_id', 'selection_epoch', 'camera_reference_epoch', 'video_sha256')
RESERVED_KEYS = frozenset(('__proto__', 'constructor', 'prototype'))


class ReportEvidenceTransportError(ValueError):
    pass


def _require(condition: bool) -> None:
    if not condition:
        raise ReportEvidenceTransportError('Report proof references are invalid or exceed transport limits')


def _json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
    except (TypeError, ValueError, OverflowError, RecursionError) as exc:
        raise ReportEvidenceTransportError('Report contains values that cannot be transported losslessly') from exc


def _bytes(value: Any) -> int:
    return len(_json(value).encode('utf-8'))


def _canonical(value: Any) -> Any:
    # JSON/JavaScript has one number type: 0.0 and -0.0 cannot make distinct
    # pool entries that a browser then sees as duplicate complete content.
    if isinstance(value, dict):
        return {key: _canonical(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        return [_canonical(item) for item in value]
    if isinstance(value, float) and math.isfinite(value) and value.is_integer():
        return int(value)
    return value


def _safe_numbers(value: Any) -> bool:
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return True
    if isinstance(value, int):
        return abs(value) <= 2**53 - 1
    if isinstance(value, float):
        return math.isfinite(value) and (not value.is_integer() or abs(value) <= 2**53 - 1)
    if isinstance(value, list):
        return all(_safe_numbers(item) for item in value)
    if isinstance(value, dict):
        return all(isinstance(key, str) and key not in RESERVED_KEYS and _safe_numbers(item) for key, item in value.items())
    return False


def _checked_proof(value: Any) -> dict:
    _require(isinstance(value, dict) and set(value) <= set(PROOF_KEYS))
    provenance, window = value.get('measurement_provenance'), value.get('source_window')
    _require(isinstance(provenance, dict) and set(provenance) <= set(MODEL_MEASUREMENT_PROVENANCE))
    _require(isinstance(window, dict) and set(window) <= set(WINDOW_KEYS))
    bindings = window.get('source_bindings')
    _require(isinstance(bindings, list) and all(isinstance(row, dict) and set(row) <= set(SOURCE_BINDING_FIELDS + ('video_timestamp_ms',)) for row in bindings))
    _require(_safe_numbers(value))
    track = value.get('source_track_id', window.get('person_track_id'))
    _require(isinstance(track, int) and not isinstance(track, bool) and track >= 0)
    checked = validated_model_window(value, person_track_id=track)
    _require(checked is not None)
    masks = validated_geometry_mask(value.get('geometry_mask'), checked)
    _require(masks is not None)
    result = {'measurement_provenance': dict(MODEL_MEASUREMENT_PROVENANCE), 'source_window': checked,
              'geometry_mask': {key: masks[key] for key in sorted(masks)}}
    if 'source_track_id' in value:
        result['source_track_id'] = track
    return result


class _Budget:
    def __init__(self):
        self.nodes = self.bytes = self.references = 0

    def add(self, count: int):
        self.bytes += count
        _require(self.bytes <= MAX_EXPANDED_REPORT_BYTES)

    def enter(self, value: Any, depth: int):
        self.nodes += 1
        _require(self.nodes <= MAX_BASE_NODES and depth <= MAX_DEPTH)
        if isinstance(value, (list, dict)):
            self.add(2)
        else:
            _require(value is None or isinstance(value, (str, bool, int, float)))
            _require(_safe_numbers(value))
            self.add(_bytes(value) + 1)

    def key(self, key: Any):
        _require(isinstance(key, str) and key not in RESERVED_KEYS)
        self.add(_bytes(key) + 2)

    def reference(self, cost: int):
        self.references += 1
        _require(self.references <= MAX_MODEL_PROOF_REFERENCES)
        self.add(cost)


def pack_report_evidence(value: dict) -> dict:
    """Return a new pool transport, or the native input when no proofs exist."""
    _require(isinstance(value, dict))
    entries, content_ids, cache = [], {}, {}
    budget = _Budget()

    def visit(raw, depth):
        budget.enter(raw, depth)
        if isinstance(raw, list):
            return [visit(item, depth + 1) for item in raw]
        if not isinstance(raw, dict):
            return raw
        _require(not any(key in raw for key in (REF, POOL, CODEC, TRANSPORT)))
        remaining, identifier = raw, None
        provenance = raw.get('measurement_provenance')
        if isinstance(provenance, dict) and provenance.get('pose_source') == 'sam3d' and raw.get('source_window') and raw.get('geometry_mask'):
            candidate = {key: raw[key] for key in PROOF_KEYS if key in raw}
            signature = _json(candidate)
            cached = cache.get(signature)
            if cached is None:
                proof = _checked_proof(candidate)
                canonical = _json(_canonical(proof))
                cached = proof, canonical, _bytes(proof)
                cache[signature] = cached
            proof, canonical, cost = cached
            identifier = content_ids.get(canonical)
            if identifier is None:
                _require(len(entries) < MAX_MODEL_PROOFS)
                identifier = len(entries)
                entries.append({'id': identifier, 'evidence': proof})
                content_ids[canonical] = identifier
            budget.reference(cost)
            remaining = {key: child for key, child in raw.items() if key not in PROOF_KEYS}
        result = {}
        for key, child in remaining.items():
            budget.key(key)
            result[key] = visit(child, depth + 1)
        if identifier is not None:
            # Preflight sees this emitted key/value too. Charge it now so a
            # packed report cannot exceed the same decoder budget at the edge.
            budget.key(REF)
            budget.enter(identifier, depth + 1)
            result[REF] = identifier
        return result

    packed = visit(value, 0)
    if not entries:
        return value
    return {**packed, TRANSPORT: MODEL_PROOF_CODEC, CODEC: MODEL_PROOF_CODEC, POOL: entries}


def unpack_report_evidence(value: dict) -> dict:
    """Strict API transport decoder; native values stay native and unmodified."""
    _require(isinstance(value, dict))
    encoded = any(key in value for key in (TRANSPORT, CODEC, POOL))
    proofs, costs, signatures, used = [], [], set(), set()
    if encoded:
        _require(value.get(TRANSPORT) == MODEL_PROOF_CODEC and value.get(CODEC) == MODEL_PROOF_CODEC)
        pool = value.get(POOL)
        _require(isinstance(pool, list) and 0 < len(pool) <= MAX_MODEL_PROOFS)
        for index, entry in enumerate(pool):
            _require(isinstance(entry, dict) and set(entry) == {'id', 'evidence'} and type(entry['id']) is int and entry['id'] == index)
            proof = _checked_proof(entry['evidence'])
            signature = _json(_canonical(proof))
            _require(signature not in signatures)
            signatures.add(signature)
            proofs.append(proof)
            costs.append(_bytes(proof))
    budget = _Budget()

    def preflight(raw, depth):
        budget.enter(raw, depth)
        if isinstance(raw, list):
            for item in raw:
                preflight(item, depth + 1)
        elif isinstance(raw, dict):
            if REF in raw:
                identifier = raw[REF]
                _require(encoded and type(identifier) is int and 0 <= identifier < len(proofs)
                         and not any(key in raw for key in PROOF_KEYS))
                budget.reference(costs[identifier])
                used.add(identifier)
            for key, child in raw.items():
                if depth == 0 and encoded and key in (TRANSPORT, CODEC, POOL):
                    continue
                _require(key not in (TRANSPORT, CODEC, POOL))
                budget.key(key)
                preflight(child, depth + 1)

    preflight(value, 0)
    if not encoded:
        return value
    _require(len(used) == len(proofs))

    def expand(raw):
        if isinstance(raw, list):
            return [expand(item) for item in raw]
        if not isinstance(raw, dict):
            return raw
        result = {key: expand(child) for key, child in raw.items() if key not in (REF, CODEC, POOL, TRANSPORT)}
        if REF in raw:
            result.update(proofs[raw[REF]])
        return result

    return expand(value)
