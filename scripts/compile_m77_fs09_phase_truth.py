#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from rallymate_evaluation.fs09_phase_truth import (
    FS09PhaseTruthError,
    compile_fs09_phase_truth_pack,
    ingest_fs09_phase_truth_exports,
)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Compile M77 independent annotations and adjudications.")
    parser.add_argument("--pack", type=Path, required=True)
    parser.add_argument(
        "--annotation-export",
        type=Path,
        action="append",
        default=[],
        help="Repeat exactly twice with the independently exported A/B CSV files.",
    )
    parser.add_argument(
        "--adjudication-export",
        type=Path,
        help="Third-party reviewer CSV exported after comparing A/B.",
    )
    parser.add_argument(
        "--handoff-manifest",
        type=Path,
        help="Verified public blind-handoff manifest used by all three exports.",
    )
    parser.add_argument(
        "--session-output",
        type=Path,
        help="New immutable intake directory; the blank source pack is never modified.",
    )
    args = parser.parse_args(argv)
    intake_requested = bool(
        args.annotation_export
        or args.adjudication_export
        or args.handoff_manifest
        or args.session_output
    )
    if intake_requested and not (
        len(args.annotation_export) == 2
        and args.adjudication_export is not None
        and args.handoff_manifest is not None
        and args.session_output is not None
    ):
        parser.error(
            "intake requires exactly two --annotation-export values, one "
            "--adjudication-export, one --handoff-manifest, and --session-output"
        )
    try:
        if intake_requested:
            report = ingest_fs09_phase_truth_exports(
                pack_dir=args.pack,
                annotation_exports=args.annotation_export,
                adjudication_export=args.adjudication_export,
                handoff_manifest_path=args.handoff_manifest,
                output_dir=args.session_output,
            )
        else:
            report = compile_fs09_phase_truth_pack(args.pack)
    except (FS09PhaseTruthError, OSError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps({"status": report["status"], **report["counts"], "errors": report["errors"]}, ensure_ascii=False))
    if report["status"] == "invalid_annotations":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
