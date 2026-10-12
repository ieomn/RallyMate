"""Explicit, resumable source-video reconstruction experiment (never a pose truth source)."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from rallymate_service.local_secrets import load_minimax_key
from rallymate_service.motion_replay_provider import (
    MiniMaxVideoProvider, ProviderError, RECONSTRUCTION_PROMPT, RECONSTRUCTION_PROMPT_VERSION,
    build_generation_payload,
)
from rallymate_service.motion_replay_store import sha256_path
from rallymate_service.motion_replay_worker import _validate_output


def write_state(path: Path, state: dict) -> None:
    state["updated_at"] = time.time()
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


@contextmanager
def exclusive_experiment(output: Path):
    """Reject concurrent writers; the OS releases this lock after a crash."""
    with (output / ".generation.lock").open("a+b") as handle:
        if os.name == "nt":
            import msvcrt
            if handle.seek(0, os.SEEK_END) == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                raise ValueError("This experiment is already being updated by another process.") from None
        else:
            import fcntl
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                raise ValueError("This experiment is already being updated by another process.") from None
        try:
            yield
        finally:
            if os.name == "nt":
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


def run(args: argparse.Namespace) -> dict:
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    with exclusive_experiment(output):
        return _run_locked(args, output)


def _complete(state: dict, video: Path, metadata: dict) -> None:
    state.update(status="completed", provider_status="succeeded", video_metadata=metadata, generated_sha256=sha256_path(video),
                 completed_at=time.time(), remote_failures=0)
    state.pop("error_code", None)


def _run_locked(args: argparse.Namespace, output: Path) -> dict:
    state_path = output / "generation.json"
    video = output / "generated.mp4"
    if args.command == "submit":
        if state_path.exists():
            raise ValueError("An experiment already exists here. Poll its task; never repeat an uncertain POST.")
        if video.exists():
            raise ValueError("A generated video already exists here and will not be overwritten.")
        reference = args.reference_video.resolve()
        appearance = args.appearance_image.resolve() if args.appearance_image else None
        prompt = RECONSTRUCTION_PROMPT
        if args.subject:
            prompt += "\n\n[PRIMARY SUBJECT]\n" + args.subject
        if args.evidence_hint:
            prompt += "\n\n[VERIFIED SOURCE EVIDENCE]\n" + args.evidence_hint
        # Validate everything before reserving the one allowed paid submission.
        build_generation_payload(reference, reference_image=appearance, prompt=prompt)
        request = {"prompt_version": RECONSTRUCTION_PROMPT_VERSION, "prompt": prompt,
                   "reference_video": str(reference), "reference_video_sha256": sha256_path(reference),
                   "appearance_image": str(appearance) if appearance else None,
                   "appearance_image_sha256": sha256_path(appearance) if appearance else None,
                   "model": "MiniMax-H3", "resolution": "768P", "duration_seconds": 6,
                   "source_manifest": str(args.source_manifest.resolve()) if args.source_manifest else None,
                   "source_manifest_sha256": sha256_path(args.source_manifest) if args.source_manifest else None}
        state = {"version": "motion-reconstruction-experiment-v1", "status": "submitting",
                 "created_at": time.time(), "request": request, "quality_status": "pending_review",
                 "remote_failures": 0, "used_for_scoring": False}
        key = load_minimax_key(args.data_root.resolve())
        provider = MiniMaxVideoProvider(api_key=key, allow_proxy_dns=args.allow_proxy_dns)
        with state_path.open("x", encoding="utf-8") as stream:
            json.dump(state, stream, ensure_ascii=False, indent=2)
        try:
            state["provider_task_id"] = provider.submit(reference, reference_image=appearance, prompt=prompt)
            state["status"] = "provider_processing"
            state["submitted_at"] = time.time()
        except ProviderError as exc:
            state.update(status="submit_unknown" if exc.submission_unknown else "rejected", error_code=exc.code)
        write_state(state_path, state)
    else:
        state = json.loads(state_path.read_text(encoding="utf-8"))
        if state["status"] in {"completed", "failed", "submit_unknown", "rejected"}:
            return state
        task_id = state.get("provider_task_id")
        if not task_id:
            # A crash after the durable reservation may have occurred during POST.
            # Mark that uncertainty explicitly; polling must never retry submission.
            state.update(status="submit_unknown", error_code="submission_outcome_unknown")
            write_state(state_path, state)
            return state
        if video.exists():
            if state["status"] != "downloading":
                raise ValueError("A generated video already exists here and will not be overwritten.")
            # Recover a crash between publishing a verified file and saving the
            # completion state. A final output is never downloaded over in place.
            metadata = _validate_output(video, 6)
            _complete(state, video, metadata)
            write_state(state_path, state)
            return state
        if state.get("remote_failures", 0) >= 5:
            raise ValueError("Repeated remote failures require investigation; the original task remains saved.")
        key = load_minimax_key(args.data_root.resolve())
        provider = MiniMaxVideoProvider(api_key=key, allow_proxy_dns=args.allow_proxy_dns)
        try:
            result = provider.query(task_id)
            if result["status"] in {"failed", "cancelled"}:
                state.update(status="failed", error_code="provider_generation_failed")
            elif result["status"] in {"queued", "running"}:
                state.update(status="provider_processing", provider_status=result["status"], remote_failures=0)
                state.pop("error_code", None)
            else:
                state["status"] = "downloading"
                write_state(state_path, state)
                temporary_video = output / "generated.pending.mp4"
                try:
                    provider.download(result["download_url"], temporary_video)
                    metadata = _validate_output(temporary_video, 6)
                    if video.exists():
                        raise ValueError("A generated video already exists here and will not be overwritten.")
                    temporary_video.replace(video)
                    _complete(state, video, metadata)
                finally:
                    temporary_video.unlink(missing_ok=True)
        except ProviderError as exc:
            state.update(error_code=exc.code, remote_failures=state.get("remote_failures", 0) + 1)
        write_state(state_path, state)
    return state


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["submit", "poll"])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--reference-video", type=Path)
    parser.add_argument("--appearance-image", type=Path)
    parser.add_argument("--source-manifest", type=Path)
    parser.add_argument("--subject", default="")
    parser.add_argument("--evidence-hint", default="")
    parser.add_argument("--data-root", type=Path, default=ROOT / "service_data")
    parser.add_argument("--allow-proxy-dns", action="store_true")
    args = parser.parse_args()
    if args.command == "submit" and args.reference_video is None:
        parser.error("submit requires --reference-video")
    try:
        state = run(args)
    except (ProviderError, ValueError, FileNotFoundError) as exc:
        raise SystemExit(exc.code if isinstance(exc, ProviderError) else str(exc)) from None
    print(json.dumps({key: value for key, value in state.items() if key != "request"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
