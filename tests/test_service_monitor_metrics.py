from __future__ import annotations

import importlib.util
import json
import math
from pathlib import Path
import subprocess
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


SPEC = importlib.util.spec_from_file_location(
    "service_monitor_metrics", Path(__file__).resolve().parents[1] / "scripts/service_monitor_metrics.py")
metrics = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(metrics)


def counters(rx, tx):
    return SimpleNamespace(bytes_recv=rx, bytes_sent=tx)


class HostMetricsTests(unittest.TestCase):
    def setUp(self):
        self.psutil = Mock()
        self.psutil.cpu_percent.return_value = 25.0
        self.psutil.virtual_memory.return_value = SimpleNamespace(total=16000, available=6000)
        self.psutil.net_io_counters.return_value = {"Ethernet": counters(1000, 2000)}
        self.psutil.net_if_stats.return_value = {"Ethernet": SimpleNamespace(isup=True)}
        self.patch_psutil = patch.object(metrics, "_psutil", self.psutil)
        self.patch_gpu = patch.object(metrics.shutil, "which", return_value=None)
        self.patch_psutil.start()
        self.patch_gpu.start()
        self.addCleanup(self.patch_psutil.stop)
        self.addCleanup(self.patch_gpu.stop)
        self.sampler = metrics.HostMetrics()

    def sample(self, now):
        value = self.sampler.sample(now)
        json.dumps(value, allow_nan=False)
        return value

    def test_first_sample_has_no_rate_or_cpu_and_second_uses_actual_elapsed_time(self):
        first = self.sample(100)
        self.assertIsNone(first["cpu"]["percent"])
        self.assertIsNone(first["network"]["interfaces"][0]["rx_bytes_per_second"])
        self.psutil.net_io_counters.return_value = {"Ethernet": counters(1600, 2300)}
        second = self.sample(103)
        row = second["network"]["interfaces"][0]
        self.assertEqual((row["rx_bytes_per_second"], row["tx_bytes_per_second"]), (200, 100))
        self.assertEqual(row["interval_seconds"], 3)
        self.assertEqual(second["cpu"]["percent"], 25)
        self.psutil.cpu_percent.assert_called_with(interval=None)
        self.psutil.net_io_counters.assert_called_with(pernic=True, nowrap=False)
        self.assertEqual(second["memory"], {"status": "ok", "used_bytes": 10000, "total_bytes": 16000, "percent": 62.5})
        third = self.sample(105)
        self.assertEqual(third["network"]["interfaces"][0]["rx_bytes_per_second"], 0)

    def test_physical_vpn_and_virtual_counters_are_never_added_together(self):
        self.psutil.net_io_counters.return_value = {name: counters(10, 20) for name in ("WLAN", "Mihomo", "vEthernet")}
        self.sample(1)
        self.psutil.net_io_counters.return_value = {name: counters(110, 220) for name in ("WLAN", "Mihomo", "vEthernet")}
        network = self.sample(3)["network"]
        self.assertEqual(len(network["interfaces"]), 3)
        self.assertTrue(all(row["rx_bytes_per_second"] == 50 for row in network["interfaces"]))
        self.assertNotIn("rx_bytes_per_second", network)
        self.assertNotIn("total", network)
        self.assertIn("包含其他软件", network["note"])
        self.assertIn("不代表对端上行速度", network["note"])

    def test_explicit_interface_filters_rows_and_missing_interface_is_unavailable(self):
        self.sampler = metrics.HostMetrics(interface="Ethernet")
        self.psutil.net_io_counters.return_value["VPN"] = counters(1000000, 2000000)
        self.assertEqual([row["name"] for row in self.sample(1)["network"]["interfaces"]], ["Ethernet"])
        self.sampler = metrics.HostMetrics(interface="missing")
        network = self.sample(1)["network"]
        self.assertEqual(network["reason"], "interface_not_found")
        self.assertEqual(network["interfaces"], [])
        for value in ("", "   ", 1):
            with self.assertRaises(ValueError):
                metrics.HostMetrics(interface=value)

    def test_counter_rollbacks_and_interface_changes_reset_both_rates(self):
        self.sample(1)
        self.psutil.net_io_counters.return_value = {"Ethernet": counters(5, 2200)}
        row = self.sample(3)["network"]["interfaces"][0]
        self.assertEqual(row["reason"], "counter_reset")
        self.assertIsNone(row["tx_bytes_per_second"])
        self.psutil.net_io_counters.return_value = {"Ethernet": counters(105, 2400)}
        self.assertEqual(self.sample(5)["network"]["interfaces"][0]["rx_bytes_per_second"], 50)
        self.psutil.net_io_counters.return_value["VPN"] = counters(2000, 3000)
        self.assertTrue(all(row["rx_bytes_per_second"] is None for row in self.sample(7)["network"]["interfaces"]))
        del self.psutil.net_io_counters.return_value["VPN"]
        self.assertIsNone(self.sample(9)["network"]["interfaces"][0]["rx_bytes_per_second"])

    def test_link_disconnect_reconnect_needs_a_fresh_baseline(self):
        self.sample(1)
        self.psutil.net_if_stats.return_value["Ethernet"].isup = False
        row = self.sample(3)["network"]["interfaces"][0]
        self.assertEqual(row["status"], "down")
        self.assertIsNone(row["rx_bytes_per_second"])
        self.psutil.net_if_stats.return_value["Ethernet"].isup = True
        self.assertEqual(self.sample(5)["network"]["interfaces"][0]["reason"], "interface_changed")

    def test_clock_rollback_same_timestamp_and_invalid_clock_reset(self):
        self.sample(10)
        for timestamp in (10, 9, math.nan, math.inf, True, 10 ** 400):
            network = self.sample(timestamp)["network"]
            self.assertTrue(all(row["rx_bytes_per_second"] is None for row in network["interfaces"]))
        self.assertIsNone(self.sample(11)["network"]["interfaces"][0]["rx_bytes_per_second"])

    def test_invalid_counter_and_sampling_error_do_not_leak_old_rates(self):
        self.sample(1)
        for value in (math.nan, math.inf, -1, True):
            self.psutil.net_io_counters.return_value = {"Ethernet": counters(value, 100)}
            row = self.sample(3)["network"]["interfaces"][0]
            self.assertEqual(row["status"], "unavailable")
            self.assertIsNone(row["rx_bytes_per_second"])
        self.psutil.net_io_counters.side_effect = OSError("private error")
        self.assertEqual(self.sample(5)["network"]["reason"], "network_sample_failed")
        self.psutil.net_io_counters.side_effect = None
        self.psutil.net_io_counters.return_value = {"Ethernet": counters(10000, 20000)}
        self.assertIsNone(self.sample(7)["network"]["interfaces"][0]["rx_bytes_per_second"])

    def test_missing_psutil_and_invalid_cpu_ram_are_explicitly_unavailable(self):
        with patch.object(metrics, "_psutil", None):
            unavailable = metrics.HostMetrics().sample(1)
        for key in ("cpu", "memory", "network"):
            self.assertEqual(unavailable[key]["reason"], "psutil_unavailable")
        self.psutil.cpu_percent.return_value = math.nan
        self.psutil.virtual_memory.return_value = SimpleNamespace(total=4, available=5)
        invalid = self.sample(1)
        self.assertIsNone(invalid["cpu"]["percent"])
        self.assertIsNone(invalid["memory"]["used_bytes"])
        self.assertNotIn("private", json.dumps(invalid))

    def test_gpu_queries_are_cached_ten_seconds_hidden_and_bounded(self):
        completed = SimpleNamespace(returncode=0, stdout='0, "GPU, Test", 34, 1024, 16384\n1, GPU Two, 0, 0, 8192\n')
        with patch.object(metrics.shutil, "which", return_value="nvidia-smi"), \
                patch.object(metrics.subprocess, "run", return_value=completed) as run:
            first = self.sample(1)["gpu"]
            self.assertEqual(first["devices"][0]["memory_used_bytes"], 1024 * 1048576)
            self.assertEqual(first["devices"][0]["name"], "GPU, Test")
            self.assertEqual(first["devices"][1]["utilization_percent"], 0)
            first["devices"][0]["name"] = "mutated"
            cached = self.sample(10)["gpu"]
            self.assertEqual(cached["sample_age_seconds"], 9)
            self.assertEqual(cached["devices"][0]["name"], "GPU, Test")
            self.assertEqual(run.call_count, 1)
            self.sample(11)
            self.assertEqual(run.call_count, 2)
            self.assertLessEqual(run.call_args.kwargs["timeout"], 1)
            self.assertFalse(run.call_args.kwargs.get("shell", False))
            if metrics.os.name == "nt":
                self.assertEqual(run.call_args.kwargs["creationflags"], subprocess.CREATE_NO_WINDOW)

    def test_gpu_failure_is_cached_and_never_returns_stale_success_values(self):
        good = SimpleNamespace(returncode=0, stdout="0, GPU, 25, 1024, 8192\n")
        with patch.object(metrics.shutil, "which", return_value="nvidia-smi"), \
                patch.object(metrics.subprocess, "run", side_effect=[good, subprocess.TimeoutExpired("nvidia-smi", 0.8)]) as run:
            self.assertEqual(self.sample(0)["gpu"]["status"], "ok")
            failed = self.sample(10)["gpu"]
            self.assertEqual(failed["reason"], "nvidia_smi_timeout")
            self.assertEqual(failed["devices"], [])
            self.sample(12)
            self.assertEqual(run.call_count, 2)

    def test_unsupported_gpu_fields_remain_null_and_malformed_output_is_rejected(self):
        with patch.object(metrics.shutil, "which", return_value="nvidia-smi"), \
                patch.object(metrics.subprocess, "run") as run:
            for index, text in enumerate(("0, GPU, N/A, N/A, N/A", "0, GPU, nan, -1, inf")):
                run.return_value = SimpleNamespace(returncode=0, stdout=text)
                device = self.sample(index * 10)["gpu"]["devices"][0]
                self.assertIsNone(device["utilization_percent"])
                self.assertIsNone(device["memory_used_bytes"])
            run.return_value = SimpleNamespace(returncode=0, stdout="not gpu data")
            self.assertEqual(self.sample(20)["gpu"]["reason"], "invalid_gpu_response")


if __name__ == "__main__":
    unittest.main()
