"""Copy completed scoring inputs to a local immutable comparison snapshot.

The live service may still update summary.json when a 3D preview finishes.
This snapshot prevents that metadata update from changing a before/after input.
No videos, database rows or live run files are modified.
"""
import argparse
import json
from pathlib import Path
import shutil


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--manifest", type=Path, required=True)
    p.add_argument("--run-root", type=Path, default=Path("service_data/runs"))
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    if args.output.exists():
        raise ValueError("snapshot already exists; choose a fresh destination")
    args.output.mkdir(parents=True)
    jobs = {r.get("job_id") for r in json.loads(args.manifest.read_text(encoding="utf-8"))["samples"]}
    frozen = []
    for job in sorted(j for j in jobs if j):
        if Path(job).name != job or "/" in job or "\\" in job or ":" in job:
            raise ValueError("job ID must be a single directory name")
        source, destination = args.run_root / job, args.output / job
        names = ("summary.json", "primary-player.jsonl", "sam3d_frames.jsonl")
        if not all((source / n).is_file() for n in names):
            frozen.append({"job_id": job, "status": "unavailable"})
            continue
        destination.mkdir()
        for _ in range(3):
            signature = lambda: [(source / n).stat().st_size for n in names] + [(source / n).stat().st_mtime_ns for n in names]
            before = signature()
            for name in names:
                shutil.copyfile(source / name, destination / name)
            if before == signature():
                json.loads((destination / "summary.json").read_text(encoding="utf-8"))
                frozen.append({"job_id": job, "status": "frozen"})
                break
        else:
            raise ValueError(f"live scoring inputs kept changing: {job}")
    (args.output / "snapshot.json").write_text(json.dumps(frozen, indent=2), encoding="utf-8")
    print(json.dumps({"frozen": sum(r["status"] == "frozen" for r in frozen), "total": len(frozen)}))


if __name__ == "__main__":
    main()
