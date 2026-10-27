"""The default producer and runtime registry must meet before expensive inference."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from rallymate_scoring.loop import run_minimum_scoring_loop, _model_versions
from rallymate_scoring.registry_lifecycle import validate_registry_lifecycle_artifacts
from rallymate_tracking import PRIMARY_PLAYER_ALGORITHM_VERSION, build_primary_player_artifacts
from rallymate_vision.contracts import ModelConfig, PipelineRequest, ScoringConfig
from rallymate_vision.pipeline import run_pipeline, validate_pipeline_primary_player_contract
from tests.test_primary_player import _record

ROOT = Path(__file__).resolve().parents[1]


class RuntimePrimaryContractTests(unittest.TestCase):
    def test_current_registry_changes_only_selector_identity_not_scoring_or_maturity(self):
        old=json.loads((ROOT/'metric-feasibility-pose-wave-v2.json').read_text())
        resolved=validate_registry_lifecycle_artifacts(ROOT/'registry-lifecycle.json')
        current=resolved['roles.runtime_feasibility'].payload
        restored=deepcopy(current)
        restored['registry_version']=old['registry_version']
        restored['updated_at']=old['updated_at']
        for item in restored['indicators']:
            self.assertEqual(item['versions']['primary_player'],PRIMARY_PLAYER_ALGORITHM_VERSION)
            item['versions']['primary_player']='primary-player-v0.3.0'
        self.assertEqual(restored,old)
        # Archived source bytes remain exactly the prior declared artifacts.
        self.assertEqual(hashlib.sha256((ROOT/'metric-feasibility-pose-wave-v2.json').read_bytes()).hexdigest(),
                         'dc5f3622d70f22ad5da8e933dcd38e7d8c4c57fd5255941fa521704984feac0e')
        self.assertEqual(hashlib.sha256((ROOT/'indicator-scoring-requirements-pose-wave-v1.json').read_bytes()).hexdigest(),
                         'd76216d543abc147be4451aa915eb43e0cae61d710277b717d1944716c8bbf04')
        for slot in ['roles.runtime_feasibility','roles.current_scoring_requirements']:
            self.assertNotIn(b'\r\n',resolved[slot].raw_bytes)

    def test_all_registered_runtime_versions_match_actual_implementations(self):
        registry=validate_registry_lifecycle_artifacts(ROOT/'registry-lifecycle.json')['roles.runtime_feasibility'].payload
        validate_pipeline_primary_player_contract(registry)
        implemented=_model_versions({},registry['registry_version'],PRIMARY_PLAYER_ALGORITHM_VERSION,registry['indicators'])
        for indicator in registry['indicators']:
            expected=indicator['versions']
            for key,actual_key in [('event_contract','event_contract'),('event_detector','event'),
                                   ('phase_contract','phase_contract'),('primary_player','primary_player'),
                                   ('quality_policy','quality_policy'),('coordinate_contract','coordinate_contract')]:
                with self.subTest(indicator=indicator['indicator_id'],contract=key):
                    self.assertEqual(expected[key],implemented[actual_key])
            self.assertEqual(expected['feature_contract'],implemented['feature_contract'][indicator['indicator_id']])

    def test_actual_current_selector_artifacts_finish_default_scoring_loop(self):
        registry=validate_registry_lifecycle_artifacts(ROOT/'registry-lifecycle.json')['roles.runtime_feasibility']
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);frames=root/'frames.jsonl';timeline=root/'primary-player.jsonl'
            records=[_record(i,[(10,[20,10,140,115],True)]) for i in range(16)]
            frames.write_text(''.join(json.dumps(x)+'\n' for x in records),encoding='utf-8')
            build_primary_player_artifacts(frames,timeline,root/'primary-player-summary.json')
            rows=[json.loads(x) for x in timeline.read_text().splitlines()]
            self.assertEqual({x['selection_algorithm_version'] for x in rows},{PRIMARY_PLAYER_ALGORITHM_VERSION})
            result=run_minimum_scoring_loop(frames_path=frames,primary_timeline_path=timeline,
                output_dir=root/'loop',feasibility_registry_path=registry.path,feasibility_registry=registry.payload,
                source_id='generated-current-selector',pose_model={'backend':'constructed_cpu_fixture'})
            self.assertEqual(result['summary']['model_versions']['primary_player'],PRIMARY_PLAYER_ALGORITHM_VERSION)
            self.assertTrue((root/'loop/scoring-loop-summary.json').exists())
            self.assertTrue(all(s.get('grade') is None for s in result['scores']))
            # Historical replay remains strict: it cannot silently claim v0.3.
            with self.assertRaisesRegex(ValueError,'timeline version does not match'):
                run_minimum_scoring_loop(frames_path=frames,primary_timeline_path=timeline,
                    output_dir=root/'wrong-loop',feasibility_registry_path=ROOT/'metric-feasibility-pose-wave-v2.json',
                    source_id='incompatible-replay')

    def test_explicit_old_registry_is_rejected_before_gpu_or_run_creation(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);video=root/'source.mp4';video.write_bytes(b'never decoded')
            request=PipelineRequest('version-preflight',video,root/'run',ModelConfig(root/'detector',root/'pose'),
                scoring=ScoringConfig(feasibility_registry=ROOT/'metric-feasibility-pose-wave-v2.json'))
            with patch('rallymate_vision.pipeline.Yolo26Perception') as model, \
                 patch('rallymate_vision.pipeline.probe_video') as decoder:
                with self.assertRaisesRegex(ValueError,'producer does not match'):
                    run_pipeline(request)
                model.assert_not_called();decoder.assert_not_called()
            self.assertFalse(request.output_dir.exists())


if __name__=='__main__':unittest.main()
