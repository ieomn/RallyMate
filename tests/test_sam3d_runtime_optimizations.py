"""Runtime binding boundaries, independent of CUDA and optional SAM packages."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from rallymate_vision.sam3d_runner import Sam3DEngine, configure_runtime_optimizations


class RuntimeOptimizationsTest(unittest.TestCase):
    def test_complete_decoder_requires_head_binding_before_any_mutation(self):
        with patch("importlib.import_module") as load:
            with self.assertRaisesRegex(ValueError, "validated body-head"):
                configure_runtime_optimizations(object(), inference_type="body", prompt_enabled=True,
                    rotation_enabled=True, decoder_graph_enabled=True)
            load.assert_not_called()

    def test_transfer_and_complete_decoder_use_current_instances_and_restore_on_failure(self):
        restored = []
        def handle(name, candidate):
            return SimpleNamespace(receipt={"candidate": candidate}, restore=lambda: restored.append(name))
        rotation = handle("rotation", "rotation")
        head = handle("head", "single_person_body_head_cuda_graph_v1")
        forward = handle("forward", "pinned_backbone_decoder_layers_cuda_graph_v1")
        transfer = handle("transfer", "pinned_all_tensor_d2h_single_wait_v1")
        output_module = SimpleNamespace(apply_output_transfer=Mock(return_value=transfer))
        decoder_module = SimpleNamespace(apply_decoder_cuda_graph=Mock(side_effect=ValueError("decoder source identity")))
        modules = [SimpleNamespace(apply_roma_fixedshape=Mock(return_value=rotation)),
                   SimpleNamespace(apply_head_cuda_graph=Mock(return_value=head)),
                   SimpleNamespace(apply_forward_cuda_graph=Mock(return_value=forward)),
                   output_module, decoder_module]
        model, estimator = object(), object()
        with patch("importlib.import_module", side_effect=modules):
            with self.assertRaisesRegex(ValueError, "decoder source identity"):
                configure_runtime_optimizations(model, estimator=estimator, inference_type="body",
                    prompt_enabled=False, rotation_enabled=True, graph_enabled=True,
                    forward_graph_scope="backbone_decoder_layers", output_transfer_enabled=True,
                    decoder_graph_enabled=True)
        output_module.apply_output_transfer.assert_called_once_with(estimator, inference_type="body")
        decoder_module.apply_decoder_cuda_graph.assert_called_once_with(model, head_handle=head, forward_handle=forward)
        self.assertEqual(restored, ["transfer", "forward", "head", "rotation"])

    def test_full_mode_excludes_new_transfer_and_complete_decoder(self):
        with patch("importlib.import_module") as load:
            handles, metadata = configure_runtime_optimizations(object(), estimator=object(),
                inference_type="full", prompt_enabled=True, rotation_enabled=True, graph_enabled=True,
                output_transfer_enabled=True, decoder_graph_enabled=True)
        load.assert_not_called()
        self.assertEqual(handles, [])
        self.assertNotIn("decoder_cuda_graph", metadata)
        self.assertNotIn("output_transfer", metadata)

    def test_dataflow_bindings_use_current_instances_and_selected_scope(self):
        graph_handle = SimpleNamespace(receipt={"candidate": "graph"}, restore=Mock())
        preprocess_handle = SimpleNamespace(receipt={"candidate": "preprocess"}, restore=Mock())
        graph = SimpleNamespace(apply_forward_cuda_graph=Mock(return_value=graph_handle))
        preprocess = SimpleNamespace(apply_gpu_preprocess=Mock(return_value=preprocess_handle))
        model, estimator = object(), object()
        with patch("importlib.import_module", side_effect=[graph, preprocess]):
            handles, metadata = configure_runtime_optimizations(
                model, estimator=estimator, inference_type="body", prompt_enabled=False,
                rotation_enabled=False, forward_graph_scope="backbone_decoder_layers",
                gpu_preprocess_enabled=True)
        graph.apply_forward_cuda_graph.assert_called_once_with(model, scope="backbone_decoder_layers")
        preprocess.apply_gpu_preprocess.assert_called_once_with(estimator, inference_type="body")
        self.assertEqual(handles, [graph_handle, preprocess_handle])
        self.assertTrue(metadata["forward_cuda_graph"]["enabled"])
        self.assertTrue(metadata["gpu_preprocess"]["enabled"])

    def test_preprocess_failure_restores_installed_forward_graph(self):
        handle = SimpleNamespace(receipt={}, restore=Mock())
        graph = SimpleNamespace(apply_forward_cuda_graph=Mock(return_value=handle))
        preprocess = SimpleNamespace(apply_gpu_preprocess=Mock(side_effect=ValueError("crop identity")))
        with patch("importlib.import_module", side_effect=[graph, preprocess]):
            with self.assertRaisesRegex(ValueError, "crop identity"):
                configure_runtime_optimizations(object(), estimator=object(), inference_type="body",
                    prompt_enabled=False, rotation_enabled=False, forward_graph_scope="backbone",
                    gpu_preprocess_enabled=True)
        handle.restore.assert_called_once_with()

    def test_unknown_forward_scope_fails_before_mutation(self):
        with patch("importlib.import_module") as load:
            with self.assertRaises(ValueError):
                configure_runtime_optimizations(object(), inference_type="body", prompt_enabled=True,
                    rotation_enabled=True, forward_graph_scope="unknown")
            load.assert_not_called()

    def test_full_mode_does_not_install_body_dataflow_bindings(self):
        with patch("importlib.import_module") as load:
            handles, metadata = configure_runtime_optimizations(object(), estimator=object(),
                inference_type="full", prompt_enabled=True, rotation_enabled=True,
                graph_enabled=True, forward_graph_scope="backbone_decoder_layers", gpu_preprocess_enabled=True)
            load.assert_not_called()
        self.assertEqual(handles, [])
        self.assertNotIn("forward_cuda_graph", metadata)
        self.assertNotIn("gpu_preprocess", metadata)

    def test_disabled_and_full_modes_never_import_optional_dependencies(self):
        for inference_type, enabled in (("body", False), ("full", True)):
            with self.subTest(inference_type=inference_type), patch("importlib.import_module") as load:
                handles, metadata = configure_runtime_optimizations(
                    object(), inference_type=inference_type,
                    prompt_enabled=enabled, rotation_enabled=enabled)
                load.assert_not_called()
                self.assertEqual(handles, [])
                self.assertTrue(all(not value["enabled"] for value in metadata.values()))
                self.assertTrue(all(value["requested"] == enabled for value in metadata.values()))

    def test_second_setup_failure_restores_first_instance(self):
        handle = SimpleNamespace(receipt={"proof": "source-pinned"}, restore=Mock())
        first = SimpleNamespace(apply_prompt_vectorized=Mock(return_value=handle))
        second = SimpleNamespace(apply_roma_fixedshape=Mock(side_effect=ValueError("source identity differs")))
        model = object()
        with patch("importlib.import_module", side_effect=[first, second]):
            with self.assertRaisesRegex(ValueError, "source identity"):
                configure_runtime_optimizations(model, inference_type="body",
                                                prompt_enabled=True, rotation_enabled=True)
        first.apply_prompt_vectorized.assert_called_once_with(model)
        second.apply_roma_fixedshape.assert_called_once_with(model)
        handle.restore.assert_called_once_with()

    def test_metadata_names_each_actual_binding_and_close_restores_reverse_order(self):
        restored = []
        first_handle = SimpleNamespace(receipt={"source": "prompt", "enabled": False},
                                       restore=lambda: restored.append("prompt"))
        second_handle = SimpleNamespace(receipt={"source": "rotation"},
                                        restore=lambda: restored.append("rotation"))
        first = SimpleNamespace(apply_prompt_vectorized=lambda _: first_handle)
        second = SimpleNamespace(apply_roma_fixedshape=lambda _: second_handle)
        with patch("importlib.import_module", side_effect=[first, second]):
            handles, metadata = configure_runtime_optimizations(
                object(), inference_type="body", prompt_enabled=True, rotation_enabled=True)
        self.assertTrue(metadata["prompt_vectorization"]["enabled"])
        self.assertEqual(metadata["rotation_vectorization"]["source"], "rotation")
        engine = Sam3DEngine.__new__(Sam3DEngine)
        engine.optimization_handles, engine.estimator = handles, object()
        engine.torch = SimpleNamespace(cuda=SimpleNamespace(empty_cache=Mock()))
        engine.close()
        engine.close()
        self.assertEqual(restored, ["rotation", "prompt"])
        self.assertIsNone(engine.estimator)


if __name__ == "__main__":
    unittest.main()
