"""Fail-closed RallyMate fine-tune overlay for RTMPose-X / Halpe26.

This file is a parseable template. ``rallymate_training.mmpose_finetune``
binds every ``__RALLYMATE_*__`` value to an audited dataset and writes a
resolved, immutable run config.  The three dataloaders deliberately delete
the upstream Body8 ``CombinedDataset`` definitions and use one COCO-style
RallyMate dataset only.
"""

_base_ = [
    '../../runtime/rtmpose/.venv/Lib/site-packages/mmpose/.mim/configs/'
    'body_2d_keypoint/rtmpose/body8/'
    'rtmpose-x_8xb256-700e_body8-halpe26-384x288.py'
]

# These tokens are replaced only after dataset/hash/isolation gates pass.
rallymate_dataset_root = '__RALLYMATE_DATASET_ROOT__'
rallymate_train_annotation = '__RALLYMATE_TRAIN_ANNOTATION__'
rallymate_val_annotation = '__RALLYMATE_VAL_ANNOTATION__'
rallymate_image_root = '__RALLYMATE_IMAGE_ROOT__'
rallymate_partial_keypoint_mask_policy = 'COCO_v0_has_zero_SimCC_target_weight'
rallymate_holdout_policy = 'validation_alias_only_no_sealed_holdout'
rallymate_accuracy_claim = False
rallymate_model_promoted = False

# The approved RTMPose-X checkpoint is verified byte-for-byte by the adapter.
load_from = '../../models/rtmpose/rtmpose-x_halpe26_384x288.pth'
work_dir = '__RALLYMATE_WORK_DIR__'

# Use MMPose's local CSPNeXt implementation and disable the upstream remote
# backbone init. The already hash-pinned full RTMPose-X checkpoint is the only
# initialization source for this fine-tune.
default_scope = 'mmpose'
model = dict(backbone=dict(_scope_='mmpose', init_cfg=None))

# Local fine-tuning defaults: bounded schedule, small batch, deterministic seed.
max_epochs = 40
base_lr = 5e-5
train_batch_size = 2
val_batch_size = 2
train_cfg = dict(max_epochs=max_epochs, val_interval=1)
randomness = dict(seed=20260904, deterministic=True, diff_rank_seed=False)
auto_scale_lr = dict(enable=False, base_batch_size=train_batch_size)

optim_wrapper = dict(
    _delete_=True,
    type='AmpOptimWrapper',
    loss_scale='dynamic',
    optimizer=dict(type='AdamW', lr=base_lr, weight_decay=0.05),
    clip_grad=dict(max_norm=35, norm_type=2),
    paramwise_cfg=dict(
        norm_decay_mult=0, bias_decay_mult=0, bypass_duplicate=True))

param_scheduler = [
    dict(
        type='LinearLR',
        start_factor=0.1,
        by_epoch=True,
        begin=0,
        end=1),
    dict(
        type='CosineAnnealingLR',
        eta_min=base_lr * 0.05,
        begin=1,
        end=max_epochs,
        T_max=max_epochs - 1,
        by_epoch=True),
]

rallymate_metainfo = dict(from_file='configs/_base_/datasets/halpe26.py')

# Keep the local recipe dependency-light. The official Body8 pipeline includes
# Albumentations, which is not part of the pinned RallyMate runtime. These
# transforms preserve the RTMPose/SimCC geometry without that optional package.
rallymate_train_pipeline = [
    dict(type='LoadImage', backend_args=dict(backend='local')),
    dict(type='GetBBoxCenterScale'),
    dict(type='RandomFlip', direction='horizontal'),
    # Do not recrop, shrink, shift, or rotate adjudicated samples. The adapter
    # requires every visible point to be inside bbox_px, and this preserves that
    # supervision through TopdownAffine even when a joint has only one example.
    dict(type='TopdownAffine', input_size=_base_.codec.input_size),
    dict(type='PhotometricDistortion'),
    dict(
        type='GenerateTarget',
        encoder=_base_.codec,
        use_dataset_keypoint_weights=True),
    dict(type='PackPoseInputs'),
]
rallymate_val_pipeline = [
    dict(type='LoadImage', backend_args=dict(backend='local')),
    dict(type='GetBBoxCenterScale'),
    dict(type='TopdownAffine', input_size=_base_.codec.input_size),
    dict(type='PackPoseInputs'),
]

# COCO visibility v=0 becomes keypoints_visible=0 in BaseCocoStyleDataset;
# GenerateTarget propagates that value into SimCC keypoint_weights. This is the
# partial-label mask: unlabelled joints make no contribution to the pose loss.
train_dataloader = dict(
    _delete_=True,
    batch_size=train_batch_size,
    num_workers=0,
    pin_memory=True,
    persistent_workers=False,
    sampler=dict(type='DefaultSampler', shuffle=True),
    dataset=dict(
        type='CocoDataset',
        data_root=rallymate_dataset_root,
        data_mode='topdown',
        ann_file=rallymate_train_annotation,
        data_prefix=dict(img=rallymate_image_root),
        metainfo=rallymate_metainfo,
        pipeline=rallymate_train_pipeline,
        test_mode=False))

val_dataloader = dict(
    _delete_=True,
    batch_size=val_batch_size,
    num_workers=0,
    pin_memory=True,
    persistent_workers=False,
    drop_last=False,
    sampler=dict(type='DefaultSampler', shuffle=False, round_up=False),
    dataset=dict(
        type='CocoDataset',
        data_root=rallymate_dataset_root,
        data_mode='topdown',
        ann_file=rallymate_val_annotation,
        data_prefix=dict(img=rallymate_image_root),
        metainfo=rallymate_metainfo,
        pipeline=rallymate_val_pipeline,
        test_mode=True))

# M95 has no released sealed holdout. The test loader is an explicit validation
# alias so it cannot silently consume or relabel a test/holdout split.
test_dataloader = dict(
    _delete_=True,
    batch_size=val_batch_size,
    num_workers=0,
    pin_memory=True,
    persistent_workers=False,
    drop_last=False,
    sampler=dict(type='DefaultSampler', shuffle=False, round_up=False),
    dataset=dict(
        type='CocoDataset',
        data_root=rallymate_dataset_root,
        data_mode='topdown',
        ann_file=rallymate_val_annotation,
        data_prefix=dict(img=rallymate_image_root),
        metainfo=rallymate_metainfo,
        pipeline=rallymate_val_pipeline,
        test_mode=True))

default_hooks = dict(
    checkpoint=dict(
        type='CheckpointHook',
        interval=1,
        save_best='PCK',
        rule='greater',
        max_keep_ckpts=1))

# Drop the upstream late-stage pipeline switch (its epoch was compiled for the
# original 700-epoch recipe); keep EMA only for this bounded fine-tune.
custom_hooks = [
    dict(
        type='EMAHook',
        ema_type='ExpMomentumEMA',
        momentum=0.0002,
        update_buffers=True,
        priority=49)
]

val_evaluator = [dict(type='PCKAccuracy', thr=0.1), dict(type='AUC')]
test_evaluator = val_evaluator
