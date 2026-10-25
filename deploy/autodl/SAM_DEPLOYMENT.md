# AutoDL SAM 3D Body deployment

The deployment lives at `/root/autodl-tmp/rallymate`. All newly installed
environments, source, checkpoints, caches, temporary files, test videos, job
artifacts and logs are on the AutoDL data disk. The versioned virtual environment
reuses the image's existing CUDA-enabled PyTorch. It does not modify the base
environment. See `SAM_ENVIRONMENT.md` for installation and exact versions.

The web server listens on `127.0.0.1:8000`; its same-origin proxy supplies the
server-only API key to `127.0.0.1:8001`. cloudflared runs on this same AutoDL
machine. No browser API key or model debugging port is published. The current
deployment uses a Quick Tunnel: its hostname can change after a tunnel restart.

## Start, inspect and stop

```bash
cd /root/autodl-tmp/rallymate
deploy/autodl/manage.sh start
deploy/autodl/manage.sh status
deploy/autodl/manage.sh url
deploy/autodl/manage.sh health
deploy/autodl/manage.sh logs worker
deploy/autodl/manage.sh stop
```

Supervisor keeps the processes alive after SSH disconnects. Startup must still
be invoked after an instance shutdown unless the owner configures the AutoDL
startup command. The platform's existing Jupyter and TensorBoard processes are
not managed by these scripts. Logs rotate in `service_data/ops`.

`manage.sh health` reads the monitor's latest saved snapshot. Check live HTTP
endpoints as well when accepting an update.

## Update code while preserving the public URL

Wait until running and queued jobs have finished. Stop only the three application
processes before replacing source, verify the new source manifest, build the web
application, and restart those processes:

```bash
cd /root/autodl-tmp/rallymate
.venv/bin/supervisorctl -c deploy/autodl/supervisord.conf stop api worker web
# Install and verify the approved source snapshot here.
export PATH="$PWD/tools/node/bin:$PATH"
export npm_config_cache="$PWD/runtime/cache/npm" TMPDIR="$PWD/runtime/tmp"
(cd scoring-demo-web && npm run build)
.venv/bin/supervisorctl -c deploy/autodl/supervisord.conf start api worker web
deploy/autodl/manage.sh status
deploy/autodl/manage.sh url
curl --fail http://127.0.0.1:8000/health/live
curl --fail http://127.0.0.1:8000/health/ready
```

The unchanged cloudflared and monitor processes stay running. Recheck both
health endpoints through the printed HTTPS URL too. `manage.sh restart` performs
a full shutdown and restart, including the tunnel, and can change that URL.

## Active model and explicit rollback

The deployment selects `sam3d`, DINOv3, `body`, native `mhr70`, and analysis
profile in the private `runtime.env`. `body` and `full` both emit 70 landmarks;
`full` additionally runs the hand refinement path. Changing the mode requires a
worker restart. The 90-frame measurement did not establish a task accuracy gain
for hand refinement, so it is not used to make stronger scoring claims.

The private `runtime.env.sam3d` and `runtime.env.rtmpose` files share the same
server-only API key. Neither is a repository artifact. When jobs are idle:

```bash
cd /root/autodl-tmp/rallymate
# Explicitly roll back to the already installed RTMPose preset:
cp -p deploy/autodl/runtime.env.rtmpose deploy/autodl/runtime.env
.venv/bin/supervisorctl -c deploy/autodl/supervisord.conf restart api worker web
deploy/autodl/manage.sh url
# Restore SAM after the rollback investigation:
cp -p deploy/autodl/runtime.env.sam3d deploy/autodl/runtime.env
.venv/bin/supervisorctl -c deploy/autodl/supervisord.conf restart api worker web
deploy/autodl/manage.sh url
```

There is no silent model fallback. Requests record the actual backend and model
hash. A SAM model loading failure must remain a failure, not relabeled RTMPose.

## Checkpoints and provenance

- SAM source: `b5c765a0d89d789985e186d396315e7590887b94`.
- DINOv3 source: `6876159a11b4df116f30f667f8c9888617df0751`.
- DINOv3 checkpoint: HF revision `11aaa346c7204874a1cbafe3d39a979080b2c55a`,
  SHA256 `b5a2f9d305dd02626b967aa2e86021fba07065df66ce7a7e00ffb9664f150abf`.
- ViT-H checkpoint: HF revision `f7f4ba4f67739e297f291639afb7c59041745367`,
  SHA256 `3b1cb897f4bbd977bf81cbb0b30780a9582681ac642ee112865790ceb4d66056`.
- Shared MHR asset: SHA256
  `352e271a6c42729c68554ceaea0c955e866970160c31e35506d782dc0f7377bc`.

The two model directories hardlink the identical MHR asset to conserve space.
All binary hashes were verified on the cloud machine. Local official HF device
authorization had succeeded; the initial download problem was a CDN timeout.
The successful fallback resolved short-lived official CDN URLs with the local
official client and passed them through SSH stdin. No HF access/refresh token or
authentication cache was copied to the cloud. The downloader never stores or
logs those temporary URLs.

The official model loader reports 113 missing outer-checkpoint keys for DINOv3.
An audit accounted for all: 110 are tensors already loaded from the independently
verified MHR JIT asset, two are unused hand-component backups, and one is the
zero-valued token on the unmasked input path. The JIT tensor digest was unchanged
after the outer load. No active prediction parameter was missing, and no
unexpected key or size mismatch was present in the captured log.

## Full 97-second frozen three-model benchmark

The full-video comparison uses all 2,911 original frames, indices 0–2910,
with original PTS 0–58200 at time base 1/600 (timestamps 0–97 seconds).
Its source is the same 7,351,558-byte `benchmark-input/source.mp4`, SHA256
`71d3f59b7a8b966ef7645cac412cb03679376f70a2f080d270391a32697d7fa9`.
The full manifest SHA256 is
`c09c36c314d86eb356efbfd60d137aeb15c434a501d12b0257863dcf627b96bf`.
PyAV 16.0.1 decodes sequentially from the source start, without seeking or
deriving timestamps from FPS; each request binds its exact decoded RGB hash,
PTS, box, source tracker ID, selection epoch and camera reference epoch.

The manifest retains 2,901 `ready` requests and 10 skipped requests with reason
`selection_missing_or_ambiguous`. The skipped frame indices are 675, 681,
859–865 and 875. All counts use the complete 2,911-request denominator. The
historical selection has 29 state segments and 11 distinct non-null tracker
IDs; neither the segments nor the IDs are human identity ground truth. All
three models receive the same historical selected boxes. Each model performs
new pose inference; historical RTMPose poses are not comparison predictions.
This protocol compares poses under fixed detection and selection inputs. It
does not measure new end-to-end detection, tracking or identity accuracy.

The private run root is
`/root/autodl-tmp/rallymate/.codex_tmp/sam-full-benchmark-20261018`.
Its uploaded `input/manifest.json`, `input/benchmark_official.py`,
`upload-manifest.json`, `frozen_full_benchmark.py` and
`start-frozen-full-benchmark.sh` define the launch. The uploaded runner SHA256 is
`f3df0ebba70a0767de39e230bb1431391303d69dba788674409fb1f8714f0b4c`.
The launcher checks that public queued/running/cancelling jobs are absent,
then freezes the private runner override into
`project/runtime/sam3d/benchmark_official.py` before starting the copied
`frozen_driver.py` in the background. It never loads `runtime.env`, changes the
public source, or stops/restarts public services. SSH disconnection does not
terminate the background controller.

`freeze.json` records 634 frozen project/configuration/provenance files,
including the copied driver and the complete MMPose configuration inheritance
tree, plus individual hashes for 265 shared official SAM/DINOv3 source files.
The standalone runner, pose wrappers and evaluator execute from the private
snapshot; production SAM adapter files are retained as provenance. The shared
official repositories and verified checkpoints are reused read-only. Freeze
records package versions, Python identity, model configurations and checkpoint
hashes, and checks the SAM/MHR hashes against their pinned official values.
The driver verifies frozen files, the shared source inventory, input manifest,
RTMPose checkpoint, SAM configurations and package versions before and after
each model process. Preserve `freeze.json` and the frozen files together;
the public deployment manifest alone does not identify this private runner.

The order is **DINOv3 body → ViT-H body → RTMPose**, with a separate process
and one warmup call per model. RTMPose uses the frozen HALPE26 configuration,
the verified local checkpoint and the runner's `realtime` profile. The two SAM
runs omit `--baseline`; pair the three saved prediction sets offline using
exact manifest bindings. Evaluator fixes and the common scoring
replay can then run on CPU with their own recorded source hashes, without
repeating GPU inference or replacing the original raw predictions. SAM
confidence remains null, and the new RTMPose runner preserves its serialized
aggregate confidence and raw per-point scores.

For audit and reproduction, retain these paths beneath the run root:

- `input/` and `freeze.json`: immutable input bindings and execution identities.
- `project/` and `frozen_driver.py`: the actual project/configuration snapshot.
- `results/{dinov3-body,vith-body,rtmpose}/`: `report.json`, `frames.jsonl` and
  model logs, including every successful, failed or skipped request.
- `resources/<model>.process.json` and `<model>.samples.jsonl`: process and
  device observations; `logs/<model>.console.log` retains subprocess output.
- `run-status.json`, `controller-exit.json` and timestamped controller exit
  records: model return codes, report hashes and controller completion/failure.

The original launcher targets the existing run root and refuses to overwrite
its snapshot or started run. An independent reproduction must use a new run
root, the same manifest/video/runner and the archived source/configuration
identities, with explicit `--run-root` and input paths. Preserve prior evidence
instead of deleting it to rerun the launcher. A changed evaluator or dependency
snapshot must be identified separately.

Resource measurements have distinct scopes. `child_execution_wall_seconds`
covers the model child execution, including decoding, loading, warmup,
inference and report writing; controller-observed wall time additionally
includes its polling delay. The runner's synchronized per-frame timing measures
the current adapter invocation in this run, not pure model forward time; it
excludes video decoding, model loading, warmup, detector/scoring work and upload.
Linux `ru_maxrss` and `/proc` RSS measurements are host memory, not GPU memory.
The unchanged runner holds all 2,901 ready 720p
RGB frames in RAM (about 7.47 GiB before other allocations). Two-second
`nvidia-smi` samples record both total device usage and per-process usage and
can miss transient peaks. Torch allocated/reserved peaks belong to the model
process's allocator and are not interchangeable with sampled device memory.
The public worker was resident when preflight found no active public jobs:
the initial sample recorded 4,306 MiB for worker PID 6623 and 4,315 MiB total
device usage. Keep that resident allocation visible and separate from benchmark
process memory; do not charge the entire device reading to the model. Device
utilization is sampled, and the idle preflight does not prove exclusive GPU
compute throughout the run. These are measurements of this run, not a verified
exclusive-GPU or concurrent public-workload throughput result.

All three model processes finished at approximately 2026-10-18 12:56 UTC.
The verified raw-evidence download completed at 13:01:01 UTC, as recorded in
`full-benchmark-cloud/download-complete.json`. Each downloaded report's SHA256
matches the hash recorded by the frozen controller. DINOv3 body and ViT-H body
each recorded 2,901 successful predictions, zero failures and 10 input-selection skips.
RTMPose recorded 2,648 successful predictions, 253 failures and 10
input-selection skips. Each model accounts for all 2,911 requested frames.
The controller correctly ended with `status: incomplete` and exit code 2
because the RTMPose run contained failures. The frozen-code CPU diagnosis
matches all 253 failures exactly to the adapter's default minimum ROI guard:
after the 0.15 crop margin, at least one crop dimension was below 32 pixels.
The adapter returned no pose before calling the RTMPose backend, and the
benchmark's expected-one-pose check recorded a failure. These are adapter
rejections, not network crashes or evidence of RTMPose pose accuracy on those
frames. The guard was not relaxed, no GPU inference was repeated, and all raw
statuses remain unchanged.

On the **same 2,648 frames successful in all three models**, the mean/p95
adapter invocation times were 106.310/112.093 ms for DINOv3 body,
96.881/98.783 ms for ViT-H body and 11.371/13.706 ms for RTMPose. These values
exclude the 253 rejected RTMPose requests and 10 input-selection skips from
the timing subset; those requests remain in the full coverage counts above.
The timing scope and single-run GPU-residency limitations described above
apply to this comparison.

The child wall times and peak host RSS cover the whole process, including
decoding/loading. The Torch figures are the maximum per-request allocator
peaks in the full request ledger, excluding the separate load/cold/warmup
measurements; they are not whole-process-lifetime GPU peaks. These resource
figures are not restricted to the common successful timing subset:

- DINOv3 body: child wall time 355.774 s; peak host RSS 12.773 GiB; process
  Torch peak allocated/reserved memory 3.363/3.568 GiB.
- ViT-H body: child wall time 322.615 s; peak host RSS 12.024 GiB; process
  Torch peak allocated/reserved memory 2.971/3.104 GiB.
- RTMPose: child wall time 62.627 s; peak host RSS 9.055 GiB; process
  Torch peak allocated/reserved memory 0.072/0.092 GiB.

Both ankles had finite raw 2D coordinates within the image in 2,387/2,911
DINOv3 frames, 2,404/2,911 ViT-H frames and 2,564/2,911 RTMPose frames. These
counts describe geometric availability without a confidence filter. They do
not establish observed visibility, landmark accuracy, technique grades or
superiority of any model. The three actual exports were verified against one
identical export-code bundle and preserve the new predictions, independent
source evidence, and empty poses for failed/skipped requests. Their common
scoring replay and independent QA remain pending.

The fixed-time visual review illustrates this distinction. At 35 seconds the
full subject is present and all three overlays roughly follow the body. At
85 seconds only a forearm and hand remain at the image edge; RTMPose places
11 of the 12 compared body points near that hand, while most SAM body points
are outside the image. In-image coordinates alone must not authorize whole-body
measurements on this out-of-frame subject. These examples are visual checks,
not manual landmark annotations or an accuracy estimate. The original
default crop guard remains unchanged; no guard-relaxed diagnostic was needed.

The local evidence paths below are relative to
`.codex_tmp/autodl-sam-coordination-20261018/`; the raw report/frame hashes are
also recorded in the analysis summary and QA handoff:

- `full-benchmark-analysis/summary.json`, SHA256
  `549ce7b0fa2bc1901912288b76c9fd25b696677558e64da7cb6589ebec026c6d`.
- `rtmpose-full-failure-diagnosis.json`, SHA256
  `113eca2ab20bbf56a77caeb5e1cab2f18ae446ad9b8f3a0125e351b8c9baa79b`.
- `full-benchmark-analysis/expected-export-code.json`, SHA256
  `2df6e89cff9c9510c443aea8672c5de401c995d207688bb3cca408aa9f2403d4`.
- `full-benchmark-analysis/actual-export-verification.json`, SHA256
  `69266b938541aa04e81f88b46f107fc1addfa35b93338eb426b5cae7df095628`.
- `full-benchmark-cloud/freeze.json`, SHA256
  `f44e671fbd59e18a69dc9953014ad3f7f6aeaf2c77db26eb8b2fba4fe7b7875b`.
- The verified downloaded archive identified by
  `full-benchmark-cloud/download-complete.json`, SHA256
  `cddc30abe874fdd004aecf52399eac167161b7e9468a76e83a0e079b4137d000`
  (archive digest, not the download marker's digest).

## Supplementary 90-frame benchmark

The following earlier 90-frame runs are supplementary measurements only. Their
body/full timings and success counts do not establish a full-video result and
must not replace the 2,911-request comparison above.

The tested input is `benchmark-input/source.mp4` (SHA256
`71d3f59b7a8b966ef7645cac412cb03679376f70a2f080d270391a32697d7fa9`).
`benchmark-input/manifest.json` binds frames 540–629, native PTS 10800–12580,
time base 1/600, track 1, the exact decoded RGB hashes and the original selected
person boxes. It is one partially out-of-frame subject during 18–20.967 seconds.

Stop the worker after jobs are idle so a queued request cannot compete with the
benchmark. Each invocation uses a new output directory and refuses to overwrite
existing evidence:

```bash
cd /root/autodl-tmp/rallymate
.venv/bin/supervisorctl -c deploy/autodl/supervisord.conf stop worker
BENCHMARK_OUT=".codex_tmp/reproduction/$(date -u +%Y%m%dT%H%M%SZ)"
export PYTHONPATH="$PWD/src"
export XDG_CACHE_HOME="$PWD/runtime/cache"
export HF_HOME="$XDG_CACHE_HOME/huggingface"
export TORCH_HOME="$XDG_CACHE_HOME/torch"
export TMPDIR="$PWD/runtime/tmp"
export OMP_NUM_THREADS=4 MKL_NUM_THREADS=4 OPENBLAS_NUM_THREADS=4
export LD_LIBRARY_PATH="$PWD/tools/egl/usr/lib/x86_64-linux-gnu:${LD_LIBRARY_PATH:-}"
CONFIG="$(.venv/bin/python -c "from rallymate_vision.pose.presets import resolve_pose_deployment_preset; print(resolve_pose_deployment_preset('rtmpose-m-halpe26-online').config_path)")"
.venv/bin/python runtime/sam3d/benchmark_official.py baseline \
  --video benchmark-input/source.mp4 --manifest benchmark-input/manifest.json \
  --out "$BENCHMARK_OUT/rtmpose" \
  --checkpoint models/rtmpose/rtmpose-m_halpe26_256x192.pth --config "$CONFIG"
for model in dinov3 vith; do
  for mode in body full; do
    .venv/bin/python runtime/sam3d/benchmark_official.py run \
      --video benchmark-input/source.mp4 --manifest benchmark-input/manifest.json \
      --model "$model" --mode "$mode" \
      --checkpoint-dir "runtime/sam3d/checkpoints/sam-3d-body-$model" \
      --baseline "$BENCHMARK_OUT/rtmpose" \
      --out "$BENCHMARK_OUT/$model-$mode"
  done
done
.venv/bin/supervisorctl -c deploy/autodl/supervisord.conf start worker
```

The initial evidence is in `.codex_tmp/sam-benchmark/<model-mode>/` on the
cloud machine: `report.json`, `frames.jsonl` and `model.log`. Cold inference,
warmup and steady inference are separate. Peak memory is the process's Torch
allocator peak, not total device consumption. The local coordination directory
contains all five reports, `benchmark-comparison.json`, and the source snapshot
manifest. A source snapshot is a test deployment of uncommitted work; it does
not mean the code has been committed, pushed or independently accepted.

All five runs returned 90/90 successful frames. Mean measured adapter invocation
time in those runs was 11.08 ms for RTMPose, 105.30/367.18 ms for DINOv3
body/full, and 98.34/334.26 ms for ViT-H body/full. SAM process Torch allocated
peaks were about 2.97–3.39 GiB. These timings
exclude decoding, person/ball/racket detection, scoring, report work and upload.

## Supplementary public integration evidence

On 2026-10-18, a public HTTPS request uploaded the original 7,351,558-byte video
and requested the 18–21 second interval. The repeat job
`f0750137-8960-4691-88d7-7f1eb4bc2a7a` completed all 90 frames with the actual SAM
worker, including postprocessing and artifact validation. Upload took 12.37 s;
upload through completed status took 41.27 s. Report, trajectory, pose preview,
technical review and summary endpoints returned HTTP 200. Video byte-range
retrieval returned HTTP 206, and browser playback and seeking were verified.

This integration run used source manifest
`36430d207ae192667b6838ce329a8844ef8eba617d5e357177a2c018a4726b32`, a targeted QA
repair of a null-confidence readiness consumer. The original failed job and
its 90 real SAM frame records were retained as regression evidence. Final
independent acceptance must identify its own frozen source and frontend build;
this successful smoke run alone is not a full acceptance verdict.

## Evidence limits and product behavior

SAM supplies no joint confidence. Every SAM point retains `confidence: null`,
explicit model-estimate semantics, raw coordinates, original PTS, source hash,
person identity and camera/selection epochs. Every requested person/frame is
recorded as successful, failed or skipped. Only the 17 exact shared anatomical
names map into the existing body measurement vocabulary; MHR toe tips do not
stand in for another model's different foot landmarks.

The default FOV is estimated. Monocular 3D coordinates do not establish measured
world distances, ground contact or technique grades. Temporal stability, lower
model disagreement and stable bone lengths do not prove higher pose or scoring
accuracy. There is no manual truth set for this test. Finger quality, additional
camera views and broader occlusion robustness remain unvalidated.

Ball, racket and court modules retain their separate sources. The product keeps
original-video replay, court projection, five training dimensions, explanations
and advice. Removed white-mesh APIs and displays remain retired. Paid language
model advice is disabled in this deployment; no new paid provider was called.
Its unavailable state must be handled honestly by the UI.
