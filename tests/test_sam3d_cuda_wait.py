"""CUDA scheduling policy contracts without initializing a real driver."""
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

from rallymate_vision import sam3d_cuda_wait as module


class DriverFixture:
    set_flags_entry = 'cuDevicePrimaryCtxSetFlags_v2'

    def __init__(self, *, flags=0, active=False, count=1):
        self.flags, self.active, self.device_count = flags, active, count
        self.writes = []

    def count(self):
        return self.device_count

    def device(self, ordinal):
        assert ordinal == 0
        return 0

    def state(self, device):
        assert device == 0
        return self.flags, self.active

    def set_flags(self, device, flags):
        self.writes.append((device, flags))
        self.flags = flags

    def version(self):
        return 13030


class CudaWaitPolicyTests(unittest.TestCase):
    def setUp(self):
        self.env = {'RALLYMATE_SAM3D_ISOLATED_WORKER': '1', module.WAIT_MODE_ENV: 'blocking_sync'}

    def test_default_does_not_load_driver_or_require_cuda_context(self):
        with patch.object(module, '_load_driver') as load:
            self.assertIsNone(module.configure_sam_cuda_wait('cpu', env={}))
            self.assertIsNone(module.configure_sam_cuda_wait('cuda:999', env={module.WAIT_MODE_ENV:'runtime_default'}))
            self.assertIsNone(module.verify_sam_cuda_wait(None))
        load.assert_not_called()

    def test_invalid_policy_fails_before_driver_load(self):
        with patch.object(module, '_load_driver') as load:
            with self.assertRaisesRegex(module.Sam3DCudaWaitError, 'must be'):
                module.configure_sam_cuda_wait('cuda:0', env={module.WAIT_MODE_ENV:'spin'})
        load.assert_not_called()

    def test_unsupported_platform_and_nonisolated_worker_fail_before_driver(self):
        with patch.object(module, '_load_driver') as load:
            with patch.object(module.os, 'name', 'posix'):
                with self.assertRaisesRegex(module.Sam3DCudaWaitError, 'Windows'):
                    module.configure_sam_cuda_wait('cuda:0', env=self.env)
            with patch.object(module.os, 'name', 'nt'):
                with self.assertRaisesRegex(module.Sam3DCudaWaitError, 'isolated SAM'):
                    module.configure_sam_cuda_wait('cuda:0', env={module.WAIT_MODE_ENV:'blocking_sync'})
        load.assert_not_called()

    def test_device_mapping_is_explicit_and_fail_closed(self):
        with patch.object(module.os, 'name', 'nt'), patch.object(module, '_load_driver') as load:
            for device, visible in (('cuda:1', None), ('cpu', None), ('cuda:0', '1'), ('cuda:0','0,1'), ('cuda:0','')):
                env = dict(self.env)
                if visible is not None:
                    env['CUDA_VISIBLE_DEVICES'] = visible
                with self.subTest(device=device, visible=visible), self.assertRaisesRegex(module.Sam3DCudaWaitError, 'sole CUDA device 0'):
                    module.configure_sam_cuda_wait(device, env=env)
        load.assert_not_called()

    def test_multiple_devices_rejected_without_flag_write(self):
        driver = DriverFixture(count=2)
        with patch.object(module.os, 'name', 'nt'), patch.object(module, '_load_driver', return_value=driver):
            with self.assertRaisesRegex(module.Sam3DCudaWaitError, 'exactly one'):
                module.configure_sam_cuda_wait('cuda:0', env=self.env)
        self.assertEqual(driver.writes, [])

    def test_active_context_is_never_changed_even_when_flags_already_match(self):
        for flags in (0, 4):
            driver = DriverFixture(flags=flags, active=True)
            with patch.object(module.os, 'name', 'nt'), patch.object(module, '_load_driver', return_value=driver):
                with self.assertRaisesRegex(module.Sam3DCudaWaitError, 'already active'):
                    module.configure_sam_cuda_wait('cuda:0', env=self.env)
            self.assertEqual(driver.writes, [])

    @unittest.skipUnless(os.name == 'nt', 'receipt source hashing uses this Windows deployment')
    def test_only_scheduling_bits_change_and_activation_readback_is_verified(self):
        driver = DriverFixture(flags=0x209)
        with patch.object(module, '_load_driver', return_value=driver):
            receipt = module.configure_sam_cuda_wait('cuda:0', env=self.env)
            self.assertEqual(driver.writes, [(0, 0x20C)])
            self.assertEqual(receipt['scheduling_flags'], 4)
            self.assertTrue(receipt['other_flags_preserved'])
            self.assertFalse(receipt['context_reset_or_release'])
            self.assertFalse(receipt['model_precision_or_graph_modified'])
            driver.active = True
            self.assertIs(module.verify_sam_cuda_wait(receipt), receipt)
            self.assertTrue(receipt['post_activation_verified'])
            self.assertEqual(driver.writes, [(0, 0x20C)])

    def test_failed_initial_readback_is_not_silently_accepted(self):
        driver = DriverFixture()
        def ignore_write(device, flags):
            driver.writes.append((device, flags))
        driver.set_flags = ignore_write
        with patch.object(module.os, 'name', 'nt'), patch.object(module, '_load_driver', return_value=driver):
            with self.assertRaisesRegex(module.Sam3DCudaWaitError, 'readback differs'):
                module.configure_sam_cuda_wait('cuda:0', env=self.env)

    def test_post_activation_mismatch_or_inactive_context_is_rejected_without_write(self):
        for flags, active in ((0, True), (4, False)):
            driver = DriverFixture(flags=flags, active=active)
            with patch.object(module, '_load_driver', return_value=driver):
                with self.assertRaisesRegex(module.Sam3DCudaWaitError, 'not retained'):
                    module.verify_sam_cuda_wait({'native_device':0, 'flags_requested':4})
            self.assertEqual(driver.writes, [])

    def test_import_does_not_initialize_driver_or_optional_model_packages(self):
        root = Path(__file__).resolve().parents[1]
        script = """
import builtins, ctypes
original = builtins.__import__
def guarded(name, *args, **kwargs):
    if name.split('.')[0] in {'torch','numpy','sam_3d_body'}:
        raise AssertionError(name)
    return original(name,*args,**kwargs)
def forbidden(*args,**kwargs):
    raise AssertionError('driver loaded at import')
builtins.__import__ = guarded
ctypes.WinDLL = forbidden
import rallymate_vision.sam3d_cuda_wait
"""
        subprocess.run([sys.executable, '-c', script], check=True, capture_output=True,
                       env={**os.environ, 'PYTHONPATH':str(root / 'src')})


if __name__ == '__main__':
    unittest.main()
