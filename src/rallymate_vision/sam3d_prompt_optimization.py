"""Reversible instance-only optimization for the official 70-joint SAM encoder.

Fixed-shape selection replaces boolean advanced indexing. Weight tensors are
concatenated on every call, so weight updates and autograd remain observable.
The caller owns the patched instance exclusively until the handle is restored.
Importing this module does not import PyTorch or the SAM model package.
"""
from __future__ import annotations

import hashlib
import inspect
from pathlib import Path
from types import MethodType


OFFICIAL_SOURCE_SHA256 = "2bb0054bfe499d13073a696f6daa9b703d94e55ac038c3b6cce45358f16b2fd2"


def _vectorized(encoder, points, labels, original):
    # Larger joint counts can alias Python integer labels under narrow tensor
    # dtypes in the original loop. Only the verified 70-joint model is optimized.
    if encoder.num_body_joints != 70:
        return original(points, labels)

    import torch

    weights = [encoder.invalid_point_embed.weight, encoder.not_a_point_embed.weight]
    weights.extend(item.weight for item in encoder.point_embeddings)
    # These checks inspect Python metadata, never GPU tensor contents. Preserve
    # uncommon original broadcasting and heterogeneous parameter contracts.
    if (labels.shape != points.shape[:-1] or labels.is_complex()
            or len(weights) != encoder.num_body_joints + 2
            or any(w.shape != (1, encoder.embed_dim) for w in weights)
            or any(w.dtype != weights[0].dtype or w.device != weights[0].device for w in weights)):
        return original(points, labels)

    # Intentionally retain the official assertion, including empty/NaN errors.
    assert points.min() >= 0 and points.max() <= 1
    pe = encoder.pe_layer._pe_encoding(points.to(torch.float))

    invalid = labels == -2
    not_a_point = labels == -1
    integral_labels = labels.to(torch.int64)
    joint = (labels >= 0) & (labels < encoder.num_body_joints) & (labels == integral_labels)
    special = invalid | not_a_point
    known = special | joint
    # Unknown/fractional/non-finite labels must keep PE and the original mask.
    # Clamp before conversion to the table range; unknown indices select row 0
    # but are discarded by the final where, not treated as label -2.
    indices = integral_labels.clamp(0, max(0, encoder.num_body_joints - 1)) + 2
    indices = torch.where(joint, indices, 0)
    indices = torch.where(not_a_point, 1, indices)
    indices = torch.where(invalid, 0, indices)
    table = torch.cat(weights, dim=0)
    selected = table.index_select(0, indices.reshape(-1)).reshape(*labels.shape, encoder.embed_dim)
    base = torch.where(special.unsqueeze(-1), torch.zeros_like(pe), pe)
    # Official += promotes with the weight, then casts back into the PE tensor.
    # Preserve that rounding under autocast, including zero + special weights.
    added = (base + selected).to(pe.dtype)
    result = torch.where(known.unsqueeze(-1), added, pe)
    return result, labels > -2


class PromptVectorizedHandle:
    def __init__(self, records, receipt):
        self._records = records
        self.receipt = receipt
        self._restored = False

    def restore(self):
        if self._restored:
            return
        for encoder, had_instance, old_instance in reversed(self._records):
            if had_instance:
                encoder._embed_keypoints = old_instance
            else:
                delattr(encoder, "_embed_keypoints")
        self._restored = True

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.restore()


def apply_prompt_vectorized(model):
    """Bind exact official PromptEncoder instances; caller must restore the handle.

    Accepts a complete SAM model or one PromptEncoder. Only 70-joint calls use
    the optimized mathematics; other joint counts retain their original method.
    Weights, buffers, module classes, package globals and forward are unchanged.
    """
    targets = [(name, module) for name, module in model.named_modules()
               if module.__class__.__name__ == "PromptEncoder"]
    if not targets:
        raise ValueError("No official PromptEncoder instance found")
    pending = []
    for name, encoder in targets:
        if encoder.training:
            raise ValueError("Optimization requires eval mode")
        original = encoder._embed_keypoints
        source = Path(inspect.getsourcefile(original))
        if hashlib.sha256(source.read_bytes()).hexdigest() != OFFICIAL_SOURCE_SHA256:
            raise ValueError("Official prompt source identity differs")
        if original.__func__ is not encoder.__class__._embed_keypoints:
            raise ValueError("Prompt method is already overridden")
        pending.append((name, encoder, original, source))

    records = []
    try:
        for name, encoder, original, source in pending:
            had_instance = "_embed_keypoints" in encoder.__dict__
            old_instance = encoder.__dict__.get("_embed_keypoints")
            records.append((encoder, had_instance, old_instance))

            def replacement(self, points, labels, _original=original):
                return _vectorized(self, points, labels, _original)

            encoder._embed_keypoints = MethodType(replacement, encoder)
    except BaseException:
        PromptVectorizedHandle(records, {}).restore()
        raise
    return PromptVectorizedHandle(records, {
        "candidate": "dynamic_weight_gather_and_where",
        "official_source_sha256": OFFICIAL_SOURCE_SHA256,
        "candidate_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "modules": [name for name, *_ in pending],
        "optimized_num_body_joints": 70,
        "other_joint_counts_use_original": True,
        "parameter_or_buffer_replacement": False,
        "global_or_source_mutation": False,
        "original_coordinate_assertion_retained": True,
        "weights_cached": False,
        "unknown_labels_keep_original_positional_encoding": True,
    })
