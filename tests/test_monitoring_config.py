"""Monitoring is local by default; optional containers validate vendor schemas."""
from __future__ import annotations

import base64
import json
import os
import re
import shutil
import subprocess
import time
import uuid
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "deploy"
MONITORING = DEPLOY / "monitoring"
OVERLAY = yaml.safe_load((DEPLOY / "docker-compose.monitoring.yml").read_text(encoding="utf-8"))


def document(relative: str):
    return yaml.safe_load((MONITORING / relative).read_text(encoding="utf-8"))


def dashboard():
    return json.loads((MONITORING / "grafana/dashboards/rallymate.json").read_text(encoding="utf-8"))


def test_monitoring_has_only_loopback_management_ports_and_private_exporters():
    services = OVERLAY["services"]
    for service in services.values():
        assert re.search(r":[^:/]+@sha256:[a-f0-9]{64}$", service["image"])
        assert "latest" not in service["image"]
        assert "backend" in service["networks"]
        assert service.get("network_mode") != "host"
        assert all(port.startswith("127.0.0.1:") for port in service.get("ports", []))
    for name in ["node-exporter", "dcgm-exporter"]:
        assert "ports" not in services[name]
    assert services["uptime-kuma"]["profiles"] == ["uptime"]
    assert "egress" in services["alertmanager"]["networks"]
    assert "egress" in services["prometheus"]["networks"]
    assert "egress" in services["grafana"]["networks"]
    gpu = services["dcgm-exporter"]
    assert gpu["gpus"] == "all"
    assert "privileged" not in gpu
    assert not any("/var/run/docker.sock" in str(service) for service in services.values())


def test_api_scrape_uses_shared_secret_file_without_inline_credentials():
    jobs = {job["job_name"]: job for job in document("prometheus/prometheus.yml")["scrape_configs"]}
    assert jobs["rallymate-api"]["authorization"] == {
        "type": "Bearer", "credentials_file": "/run/secrets/metrics_token"
    }
    assert jobs["rallymate-api"]["follow_redirects"] is False
    assert jobs["rallymate-api"]["static_configs"][0]["targets"] == ["api:8000"]
    assert jobs["rallymate-worker"]["static_configs"][0]["targets"] == ["worker:9101"]
    assert OVERLAY["services"]["prometheus"]["secrets"] == ["metrics_token"]
    grafana = OVERLAY["services"]["grafana"]
    assert grafana["environment"]["GF_SECURITY_ADMIN_PASSWORD__FILE"] == "/run/secrets/grafana_admin_password"
    assert grafana["environment"]["GF_AUTH_ANONYMOUS_ENABLED"] == "false"
    assert not any("credentials" in job.get("authorization", {}) for job in jobs.values())


def test_default_alert_routing_sends_nothing_and_examples_are_explicit():
    default = document("alertmanager/alertmanager.yml")
    assert default["route"]["receiver"] == "local-only"
    assert default["receivers"] == [{"name": "local-only"}]
    email = document("alertmanager/email.example.yml")
    assert email["global"]["smtp_require_tls"] is True
    assert "smtp_auth_password_file" in email["global"]
    assert email["receivers"][0]["email_configs"][0]["to"].endswith(".invalid")
    for name in ["webhook", "feishu"]:
        hook = document(f"alertmanager/{name}.example.yml")["receivers"][0]["webhook_configs"][0]
        assert "url" not in hook and hook["url_file"].startswith("/etc/alertmanager/secrets/")
        assert hook["send_resolved"] is True
    feishu = document("alertmanager/feishu.example.yml")["receivers"][0]["webhook_configs"][0]
    assert feishu["payload"]["msg_type"] == "text"
    assert "RallyMate" in feishu["payload"]["content"]["text"]


def test_dashboard_is_provisioned_and_covers_api_worker_host_and_gpu_contracts():
    source = document("grafana/provisioning/datasources/prometheus.yml")["datasources"][0]
    assert source["url"] == "http://prometheus:9090"
    provider = document("grafana/provisioning/dashboards/rallymate.yml")["providers"][0]
    assert provider["options"]["path"] == "/var/lib/grafana/dashboards"
    board = dashboard()
    assert board["uid"] == "rallymate-operations"
    assert len({panel["id"] for panel in board["panels"]}) == len(board["panels"])
    for panel in board["panels"]:
        assert panel["datasource"]["uid"] == source["uid"]
    queries = "\n".join(target["expr"] for panel in board["panels"] for target in panel["targets"])
    for metric in [
        "rallymate_http_requests_total", "rallymate_http_request_duration_seconds_bucket",
        "rallymate_http_time_to_headers_seconds_bucket", "rallymate_jobs",
        "rallymate_job_attempts_total", "rallymate_database_available", "rallymate_worker_up",
        "rallymate_inference_attempt_duration_seconds_sum", "rallymate_inference_attempt_duration_seconds_count",
        "rallymate_inference_attempts_total", "node_cpu_seconds_total", "node_memory_MemAvailable_bytes",
        "DCGM_FI_DEV_GPU_UTIL_RATIO", "DCGM_FI_DEV_FB_USED_BYTES", "DCGM_FI_DEV_FB_FREE_BYTES",
    ]:
        assert metric in queries
    assert "histogram_quantile(0.95" in queries and "histogram_quantile(0.99" in queries
    assert "UUID" in board["templating"]["list"][0]["query"]["query"]
    gpu_panels = [panel for panel in board["panels"] if any("DCGM_" in target["expr"] for target in panel["targets"])]
    assert all("{{UUID}}" in target["legendFormat"] for panel in gpu_panels for target in panel["targets"])


def test_alerts_cover_failures_and_use_headers_for_latency_and_available_ram():
    rules = {rule["alert"]: rule for group in document("prometheus/alerts.yml")["groups"] for rule in group["rules"]}
    assert set(rules) >= {
        "RallyMateAPIDown", "RallyMateWorkerDown", "RallyMateDatabaseDown",
        "RallyMateCPUHigh", "RallyMateRAMHigh", "RallyMateGPUVRAMHigh",
        "RallyMateHTTP5xxHigh", "RallyMateHTTPHeadersSlow",
    }
    assert "time_to_headers_seconds_bucket" in rules["RallyMateHTTPHeadersSlow"]["expr"]
    assert "MemAvailable" in rules["RallyMateRAMHigh"]["expr"]
    assert "UUID" in rules["RallyMateGPUVRAMHigh"]["annotations"]["summary"]
    assert "> 90" in rules["RallyMateGPUVRAMHigh"]["expr"]
    assert "node-exporter" in str(OVERLAY["services"])
    node = OVERLAY["services"]["node-exporter"]
    assert set(node["command"]) == {
        "--path.procfs=/host/proc", "--path.sysfs=/host/sys", "--collector.disable-defaults",
        "--collector.cpu", "--collector.meminfo", "--collector.loadavg", "--collector.uname",
    }
    assert set(node["volumes"]) == {"/proc:/host/proc:ro", "/sys:/host/sys:ro"}


def test_dcgm_49_queries_match_collector_names_and_si_units():
    counters = (MONITORING / "dcgm/counters.csv").read_text(encoding="utf-8")
    names = {line.split(",")[0] for line in counters.splitlines() if line and not line.startswith("#")}
    gpu_queries = [target["expr"] for panel in dashboard()["panels"] for target in panel["targets"] if "DCGM_" in target["expr"]]
    used = set(re.findall(r"DCGM_[A-Z0-9_]+", " ".join(gpu_queries)))
    assert used <= names
    assert "DCGM_FI_DEV_GPU_UTIL_RATIO" in names
    assert "DCGM_FI_DEV_FB_USED_BYTES" in names
    assert all(expr.startswith("100 *") for expr in gpu_queries if "GPU_UTIL_RATIO" in expr)
    assert all("1024" not in expr for expr in gpu_queries if "FB_USED_BYTES" in expr)


@pytest.mark.skipif(os.environ.get("RALLYMATE_TEST_MONITORING_TOOLS") != "1", reason="Enable isolated vendor-tool checks explicitly")
def test_vendor_promtool_amtool_and_alert_scenarios(tmp_path):
    assert shutil.which("docker"), "Docker is required for the requested vendor-tool check"
    secret = tmp_path / "metrics_token"
    secret.write_text("synthetic-monitoring-validation-token", encoding="utf-8")
    secret.chmod(0o444)
    env = os.environ.copy()
    env["MSYS_NO_PATHCONV"] = "1"

    def run(service, tool, args, mounts, workdir=None):
        command = ["docker", "run", "--rm", "--network", "none"]
        for source, target in mounts:
            command += ["--mount", f"type=bind,source={source.resolve()},target={target},readonly"]
        if workdir:
            command += ["--workdir", workdir]
        command += ["--entrypoint", tool, OVERLAY["services"][service]["image"], *args]
        result = subprocess.run(command, text=True, capture_output=True, env=env, timeout=240)
        assert result.returncode == 0, result.stdout + result.stderr

    mounts = [(MONITORING / "prometheus", "/etc/prometheus"), (secret, "/run/secrets/metrics_token")]
    run("prometheus", "/bin/promtool", ["check", "config", "/etc/prometheus/prometheus.yml"], mounts)
    run("prometheus", "/bin/promtool", ["test", "rules", "alerts.test.yml"], mounts, "/etc/prometheus")
    expressions = []
    for panel in dashboard()["panels"]:
        for target in panel["targets"]:
            expressions.append({"record": f"rallymate_dashboard_check_{len(expressions)}", "expr": target["expr"].replace("$__rate_interval", "5m").replace("$gpu", ".*")})
    checks = tmp_path / "dashboard-rules.yml"
    checks.write_text(yaml.safe_dump({"groups": [{"name": "dashboard-check", "rules": expressions}]}), encoding="utf-8")
    run("prometheus", "/bin/promtool", ["check", "rules", "/checks/dashboard-rules.yml"], [(tmp_path, "/checks")])
    for config in (MONITORING / "alertmanager").glob("*.yml"):
        run("alertmanager", "/bin/amtool", ["check-config", f"/etc/alertmanager/{config.name}"], [(config.parent, "/etc/alertmanager")])


@pytest.mark.skipif(os.environ.get("RALLYMATE_TEST_MONITORING_TOOLS") != "1", reason="Enable isolated vendor-tool checks explicitly")
def test_grafana_loads_datasource_and_dashboard_without_host_ports(tmp_path):
    assert shutil.which("docker"), "Docker is required for the requested provisioning check"
    name = f"rallymate-grafana-check-{uuid.uuid4().hex[:12]}"
    password = "synthetic-local-provisioning-password"
    secret = tmp_path / "grafana-password"
    secret.write_text(password, encoding="utf-8")
    secret.chmod(0o444)
    command = [
        "docker", "run", "--detach", "--rm", "--name", name, "--network", "none",
        "--tmpfs", "/var/lib/grafana:uid=472,gid=472",
        "--env", "GF_SECURITY_ADMIN_PASSWORD__FILE=/run/secrets/grafana_admin_password",
        "--env", "GF_AUTH_ANONYMOUS_ENABLED=false",
        "--env", "GF_ANALYTICS_REPORTING_ENABLED=false",
        "--env", "GF_ANALYTICS_CHECK_FOR_UPDATES=false",
        "--env", "GF_PLUGINS_PREINSTALL_DISABLED=true",
    ]
    for source, target in [
        (MONITORING / "grafana/provisioning", "/etc/grafana/provisioning"),
        (MONITORING / "grafana/dashboards", "/var/lib/grafana/dashboards"),
        (secret, "/run/secrets/grafana_admin_password"),
    ]:
        command += ["--mount", f"type=bind,source={source.resolve()},target={target},readonly"]
    command.append(OVERLAY["services"]["grafana"]["image"])
    started = subprocess.run(command, text=True, capture_output=True, timeout=300)
    assert started.returncode == 0, started.stderr
    authorization = base64.b64encode(f"admin:{password}".encode()).decode()

    def request(path):
        result = subprocess.run([
            "docker", "exec", name, "wget", "-q", "-O", "-",
            f"--header=Authorization: Basic {authorization}", f"http://127.0.0.1:3000{path}",
        ], text=True, capture_output=True, timeout=10)
        return json.loads(result.stdout) if result.returncode == 0 else None

    try:
        health = None
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            health = request("/api/health")
            if health and health.get("database") == "ok":
                break
            time.sleep(1)
        assert health and health["database"] == "ok", "isolated Grafana failed to start"
        source = request("/api/datasources/uid/rallymate-prometheus")
        assert source and source["url"] == "http://prometheus:9090"
        loaded = request("/api/dashboards/uid/rallymate-operations")
        assert loaded and loaded["dashboard"]["uid"] == "rallymate-operations"
        assert len(loaded["dashboard"]["panels"]) == len(dashboard()["panels"])
        inspection = subprocess.run(["docker", "inspect", "--format", "{{json .HostConfig.PortBindings}}", name], text=True, capture_output=True, check=True)
        assert json.loads(inspection.stdout) in (None, {})
    finally:
        subprocess.run(["docker", "rm", "--force", name], capture_output=True, check=False)
