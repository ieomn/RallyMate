"""Publish evaluated, hash-bound selection sidecars without changing old poses.

All inputs are checked before writing. Original primary timelines, native poses,
frames and summaries remain untouched. Existing sidecars are never overwritten.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evaluation', type=Path, required=True)
    parser.add_argument('--contexts', type=Path, required=True)
    parser.add_argument('--runs', type=Path, required=True)
    parser.add_argument('--record', type=Path, required=True)
    parser.add_argument('--selector-evaluation', type=Path)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    evaluation = read(args.evaluation)
    if evaluation.get('error_count') or evaluation.get('code_changed_during_execution'):
        raise ValueError('Only a completed unchanged regression can be published')
    if not evaluation.get('selector_recomputed'):
        if args.selector_evaluation is None or sha(args.selector_evaluation) != evaluation.get('selector_parent_evaluation_sha256'):
            raise ValueError('Reused selection requires its content-bound full-selector evaluation')
        parent = read(args.selector_evaluation)
        if not parent.get('selector_recomputed') or parent.get('error_count') or parent.get('code_changed_during_execution'):
            raise ValueError('Invalid parent selector evaluation')
        parents = {row['sample_id']: row for row in parent['samples']}
        for row in evaluation['samples']:
            prior = parents[row['sample_id']]
            if any(row[key] != prior[key] for key in ('source_video_sha256', 'output_timeline_sha256')):
                raise ValueError('Reused timeline does not match its parent')
    if args.record.exists():
        raise FileExistsError(args.record)
    writes, receipts = [], []
    for row in evaluation['samples']:
        job, name = row['job_id'], row['sample_id']
        run = (args.runs / job).resolve()
        if run.parent != args.runs.resolve():
            raise ValueError('Unexpected run path')
        hashes = row['input_sha256']
        for filename in ('frames.jsonl', 'sam3d_frames.jsonl', 'summary.json'):
            if sha(run / filename) != hashes[filename]:
                raise ValueError(f'Original artifact changed: {job}/{filename}')
        timeline_path = args.evaluation.parent / f'{name}-primary-player.jsonl'
        if sha(timeline_path) != row['output_timeline_sha256']:
            raise ValueError('Evaluated timeline changed')
        timeline = [json.loads(line) for line in timeline_path.read_text(encoding='utf-8').splitlines() if line]
        document = {'version': 'tennis-subject-selection-v1', 'source_video_sha256': row['source_video_sha256'],
                    'source_artifact_sha256': {key: hashes[key] for key in ('sam3d_frames.jsonl', 'frames.jsonl')},
                    'selector': 'primary-player-v0.4.0', 'method': 'source_pixel_subject_lock_reselection',
                    'evaluation_sha256': sha(args.evaluation), 'timeline': timeline}
        files = {run / 'tennis-subject-selection.json': json.dumps(document, ensure_ascii=False).encode('utf-8')}
        if hashes.get('action_context'):
            context_path = args.contexts / f'{job}.json'
            if sha(context_path) != hashes['action_context']:
                raise ValueError('Evaluated action review changed')
            files[run / 'action-context.json'] = context_path.read_bytes()
        for target, content in files.items():
            if target.exists():
                raise FileExistsError(target)
            writes.append((target, content))
        receipts.append({'job_id': job, 'sample_id': name, 'original_artifact_sha256': {
            key: sha(run / key) for key in ('primary-player.jsonl', 'sam3d_frames.jsonl', 'frames.jsonl', 'summary.json')},
            'published': {target.name: hashlib.sha256(content).hexdigest() for target, content in files.items()},
            'expected_score': row['score'], 'expected_episodes': row['episodes']})
    if args.apply:
        for target, content in writes:
            with target.open('xb') as stream:
                stream.write(content)
        for receipt in receipts:
            for filename, expected in receipt['original_artifact_sha256'].items():
                if sha(args.runs / receipt['job_id'] / filename) != expected:
                    raise ValueError('Historical input changed during publication')
    record = {'version': 'tennis-review-publication-v1', 'applied': args.apply,
              'created_at_utc': datetime.now(timezone.utc).isoformat(), 'evaluation_sha256': sha(args.evaluation),
              'original_inputs_unchanged': True, 'jobs': receipts}
    args.record.parent.mkdir(parents=True, exist_ok=True)
    args.record.write_text(json.dumps(record, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'applied': args.apply, 'jobs': len(receipts), 'files': len(writes)}))


if __name__ == '__main__':
    main()
