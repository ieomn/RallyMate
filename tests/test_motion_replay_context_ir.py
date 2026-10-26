"""Contract checks for full-body reference images and the separate Context-IR task."""
from __future__ import annotations

import base64
import io
import json
import struct
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np

from rallymate_service.motion_replay_provider import (
    LEGACY_PROMPT_V1, LEGACY_PROMPT_V2, MAX_CONTEXT_IR_PROMPT_CHARS,
    MAX_GENERATION_PROMPT_CHARS, PROMPT, RECONSTRUCTION_PROMPT, RECONSTRUCTION_PROMPT_VERSION,
    MiniMaxVideoProvider, ProviderError,
    build_context_ir_payload, build_generation_payload, request_prompt,
)


class Response(io.BytesIO):
    def __init__(self, payload):
        super().__init__(json.dumps(payload).encode())
        self.headers = {}


class Opener:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def open(self, request, timeout):
        self.calls.append(request)
        return Response(self.responses.pop(0))


def task_response(*, task_id="context_123", task_type="h3_context_ir", status="succeeded", prompt="structured prompt"):
    return {"task": {"id": task_id, "task_type": task_type, "status": status, "content": {"prompt": prompt}}}


class ContextIRProviderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.video = self.root / "reference.mp4"
        self.video.write_bytes(b"\x00\x00\x00\x18ftypisom\x00\x00\x00\x00isomavc1")

    def image(self, *, suffix=".png", width=512, height=512):
        path = self.root / ("appearance" + suffix)
        encoded, content = cv2.imencode(suffix, np.full((height, width, 3), 220, dtype=np.uint8))
        self.assertTrue(encoded)
        path.write_bytes(content.tobytes())
        return path

    def test_prompt_has_distinct_body_and_motion_responsibilities_without_rewriting_old_snapshots(self):
        self.assertIn("[REFERENCE RESPONSIBILITIES]", PROMPT)
        self.assertIn("[BODY SURFACE]", PROMPT)
        self.assertIn("joint paths", PROMPT)
        self.assertIn("not the desired anatomy", PROMPT)
        self.assertLessEqual(len(PROMPT), MAX_GENERATION_PROMPT_CHARS)
        self.assertNotEqual(PROMPT, LEGACY_PROMPT_V2)
        for old in (LEGACY_PROMPT_V1, LEGACY_PROMPT_V2):
            self.assertEqual(request_prompt({"prompt": old}), old)
        self.assertEqual(request_prompt({"prompt_version": "white-mannequin-motion-v1"}), LEGACY_PROMPT_V1)
        with self.assertRaises(ProviderError):
            request_prompt({"prompt_version": "white-mannequin-motion-v2"})

    def test_image_and_video_are_independent_reference_roles_for_png_and_jpeg(self):
        for suffix, mime in ((".png", "image/png"), (".jpg", "image/jpeg"), (".jpeg", "image/jpeg")):
            with self.subTest(suffix=suffix):
                image = self.image(suffix=suffix)
                payload = build_generation_payload(self.video, reference_image=image)
                self.assertEqual([item.get("role") for item in payload["content"]], [None, "reference_video", "reference_image"])
                self.assertEqual(payload["content"][0]["text"], PROMPT)
                url = payload["content"][2]["image_url"]["url"]
                self.assertTrue(url.startswith("data:" + mime + ";base64,"))
                self.assertEqual(base64.b64decode(url.split(",", 1)[1]), image.read_bytes())

    def test_video_only_requests_keep_previous_contract(self):
        payload = build_generation_payload(self.video, prompt=LEGACY_PROMPT_V2)
        self.assertEqual(len(payload["content"]), 2)
        self.assertEqual(payload["content"][0]["text"], LEGACY_PROMPT_V2)

    def test_reconstruction_prompt_is_opt_in_and_uses_source_video_with_style_image(self):
        image = self.image()
        experiment = build_generation_payload(self.video, reference_image=image, prompt=RECONSTRUCTION_PROMPT)
        default = build_generation_payload(self.video, reference_image=image)
        self.assertNotEqual(RECONSTRUCTION_PROMPT, PROMPT)
        self.assertEqual(RECONSTRUCTION_PROMPT_VERSION, "source-video-anatomical-reconstruction-v1")
        self.assertLessEqual(len(RECONSTRUCTION_PROMPT), MAX_GENERATION_PROMPT_CHARS)
        self.assertEqual(default["content"][0]["text"], PROMPT)
        self.assertEqual(experiment["content"][0]["text"], RECONSTRUCTION_PROMPT)
        self.assertEqual(experiment["content"][1:], default["content"][1:])
        self.assertEqual([item.get("role") for item in experiment["content"]], [None, "reference_video", "reference_image"])
        self.assertEqual(request_prompt({"prompt_version": RECONSTRUCTION_PROMPT_VERSION,
                                         "prompt": RECONSTRUCTION_PROMPT}), RECONSTRUCTION_PROMPT)
        # Only documented multimedia fields are sent; prose is not a pose-control API.
        self.assertEqual(set(experiment), {"model", "content", "resolution", "duration", "ratio"})

    def test_reconstruction_prompt_distinguishes_artifact_repair_from_technique_and_projection(self):
        self.assertIn("original camera recording", RECONSTRUCTION_PROMPT)
        self.assertIn("left/right limb swaps", RECONSTRUCTION_PROMPT)
        self.assertIn("not by screen position", RECONSTRUCTION_PROMPT)
        self.assertIn("do not change the person's actual technique", RECONSTRUCTION_PROMPT)
        self.assertIn("foreshortening", RECONSTRUCTION_PROMPT)
        self.assertIn("Never force equal on-screen limb lengths", RECONSTRUCTION_PROMPT)
        self.assertIn("not motion or anatomy evidence", RECONSTRUCTION_PROMPT)
        context = build_context_ir_payload(self.video, prompt=RECONSTRUCTION_PROMPT)
        self.assertEqual(context["content"][0]["text"], RECONSTRUCTION_PROMPT)
        self.assertNotIn("resolution", context)

    def test_reference_image_dimensions_are_bounded_before_decoder_runs(self):
        image = self.image()
        data = bytearray(image.read_bytes())
        data[16:24] = struct.pack(">II", 100000, 100000)
        image.write_bytes(data)
        with patch("cv2.imdecode", side_effect=AssertionError("must not decode oversized image")):
            with self.assertRaises(ProviderError) as error:
                build_generation_payload(self.video, reference_image=image)
        self.assertEqual(error.exception.code, "reference_image_invalid")

    def test_tiny_extreme_ratio_and_corrupt_images_never_submit(self):
        for width, height in ((255, 512), (512, 255), (256, 1024), (1024, 256)):
            with self.subTest(width=width, height=height):
                with self.assertRaises(ProviderError):
                    build_generation_payload(self.video, reference_image=self.image(width=width, height=height))
        image = self.image()
        image.write_bytes(image.read_bytes()[:30])
        opener = Opener()
        provider = MiniMaxVideoProvider(api_key="private-test-key", opener=opener)
        with self.assertRaises(ProviderError):
            provider.submit(self.video, reference_image=image)
        self.assertEqual(opener.calls, [])
        jpeg = self.image(suffix=".jpg")
        jpeg.write_bytes(jpeg.read_bytes()[:-2])
        with self.assertRaises(ProviderError):
            provider.submit(self.video, reference_image=jpeg)
        self.assertEqual(opener.calls, [])

    def test_mislabeled_image_or_missing_image_has_safe_error(self):
        image = self.image(suffix=".jpg")
        mislabeled = self.root / "not-really-png.png"
        mislabeled.write_bytes(image.read_bytes())
        for invalid in (mislabeled, self.root / "private-nonexistent.png"):
            with self.subTest(path=invalid.name):
                with self.assertRaises(ProviderError) as error:
                    build_generation_payload(self.video, reference_image=invalid)
                self.assertNotIn(str(self.root), str(error.exception))

    def test_context_ir_payload_has_same_inputs_without_resolution(self):
        image = self.image()
        payload = build_context_ir_payload(self.video, reference_image=image)
        self.assertEqual(set(payload), {"model", "content", "duration", "ratio"})
        generation = build_generation_payload(self.video, reference_image=image)
        self.assertEqual(payload["content"], generation["content"])
        self.assertEqual((payload["model"], payload["duration"], payload["ratio"]), ("MiniMax-H3", 6, "16:9"))

    def test_context_ir_submission_and_query_use_separate_task_type_and_preserve_opaque_text(self):
        enhanced = 'integrated_multimodal_description: [Shot 1] Fixed shot.\noverall_soundscape: None.\n{"not_executed": true}'
        opener = Opener({"task_id": "context_123"}, task_response(prompt=enhanced))
        provider = MiniMaxVideoProvider(api_key="private-test-key", opener=opener)
        task_id = provider.submit_context_ir(self.video, reference_image=self.image())
        self.assertEqual(provider.query_context_ir(task_id), {"status": "succeeded", "prompt": enhanced})
        self.assertEqual(opener.calls[0].full_url, "https://api.minimax.io/v2/h3_context_ir")
        self.assertNotIn("resolution", json.loads(opener.calls[0].data))
        self.assertEqual(opener.calls[1].full_url, "https://api.minimax.io/v2/query/video_generation/context_123")
        self.assertEqual(len(opener.calls), 2)  # Query must not start video generation.

    def test_context_ir_rejects_mismatched_or_missing_type_id_or_invalid_status(self):
        for response in (task_response(task_id="someone_else"), task_response(task_type="generation"),
                         task_response(task_type=None), task_response(status="unknown")):
            with self.subTest(response=response):
                provider = MiniMaxVideoProvider(api_key="key", opener=Opener(response))
                with self.assertRaises(ProviderError) as error:
                    provider.query_context_ir("context_123")
                self.assertEqual(error.exception.code, "invalid_provider_response")
                self.assertTrue(error.exception.automatic_retry)

    def test_context_ir_completed_prompt_must_be_nonempty_bounded_string(self):
        for prompt in (None, "", " \n", {"prompt": "structured dict"}, 123):
            with self.subTest(prompt=prompt):
                provider = MiniMaxVideoProvider(api_key="key", opener=Opener(task_response(prompt=prompt)))
                with self.assertRaises(ProviderError) as error:
                    provider.query_context_ir("context_123")
                self.assertEqual(error.exception.code, "invalid_context_ir_response")
        provider = MiniMaxVideoProvider(api_key="key", opener=Opener(task_response(prompt="x" * (MAX_CONTEXT_IR_PROMPT_CHARS + 1))))
        with self.assertRaises(ProviderError) as error:
            provider.query_context_ir("context_123")
        self.assertEqual(error.exception.code, "context_ir_prompt_too_large")
        self.assertFalse(error.exception.automatic_retry)

    def test_context_ir_long_valid_output_is_not_silently_truncated_for_generation(self):
        enhanced = "a" * (MAX_GENERATION_PROMPT_CHARS + 1)
        provider = MiniMaxVideoProvider(api_key="key", opener=Opener(task_response(prompt=enhanced)))
        result = provider.query_context_ir("context_123")
        self.assertEqual(result["prompt"], enhanced)
        with self.assertRaises(ProviderError) as error:
            build_generation_payload(self.video, prompt=result["prompt"])
        self.assertEqual(error.exception.code, "invalid_generation_prompt")

    def test_noncomplete_context_ir_states_return_no_usable_prompt(self):
        for status in ("queued", "running", "failed", "cancelled"):
            provider = MiniMaxVideoProvider(api_key="key", opener=Opener(task_response(status=status)))
            self.assertEqual(provider.query_context_ir("context_123"), {"status": status, "prompt": None})

    def test_invalid_task_identifier_does_not_make_network_request(self):
        opener = Opener()
        provider = MiniMaxVideoProvider(api_key="key", opener=opener)
        for task_id in (None, "../private", "id?token=private", "x" * 129):
            with self.assertRaises(ProviderError):
                provider.query_context_ir(task_id)
        self.assertEqual(opener.calls, [])

    def test_context_ir_unknown_submission_is_not_automatically_retried_or_leaked(self):
        class BrokenOpener:
            def open(self, request, timeout):
                raise urllib.error.URLError("private-test-key data:video/mp4;base64,private")
        provider = MiniMaxVideoProvider(api_key="private-test-key", opener=BrokenOpener())
        with self.assertRaises(ProviderError) as error:
            provider.submit_context_ir(self.video)
        self.assertTrue(error.exception.submission_unknown)
        self.assertFalse(error.exception.automatic_retry)
        self.assertNotIn("private", str(error.exception))
        for response in ({}, {"task_id": "../invalid"}):
            provider = MiniMaxVideoProvider(api_key="key", opener=Opener(response))
            with self.assertRaises(ProviderError) as error:
                provider.submit_context_ir(self.video)
            self.assertTrue(error.exception.submission_unknown)

    def test_empty_or_oversize_input_prompt_fails_before_any_post(self):
        provider = MiniMaxVideoProvider(api_key="key", opener=Opener())
        for prompt in ("", " \n", None, "a" * (MAX_GENERATION_PROMPT_CHARS + 1)):
            with self.assertRaises(ProviderError):
                provider.submit_context_ir(self.video, prompt=prompt)
        self.assertEqual(provider._opener.calls, [])


if __name__ == "__main__":
    unittest.main()
