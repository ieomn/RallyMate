"""MiniMax H3 reference-video adapter. Never logs credentials or data URIs.

Official contract: /docs/api-reference/video-generation-v2-create.md and
/docs/api-reference/video-generation-v2-query, checked 2026-10-11.
"""
from __future__ import annotations

import base64
import http.client
import ipaddress
import json
import os
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


PROMPT = (
    "Use reference video 1 as the motion reference. Render one plain matte-white, "
    "faceless human mannequin on a clean neutral grey background, with a fixed camera. "
    "Preserve the reference person's visible body proportions, left/right limbs, "
    "joint trajectories, movement direction, timing, pauses and original imperfections. "
    "Replay only the observed motion in its original order and at the original speed. "
    "Do not correct posture, add new movements, change the action, mirror the person, "
    "invent object interactions, add text or move the camera. Keep the whole body visible. "
    "This is a visual recreation of the supplied motion, not a coaching demonstration."
)
LEGACY_PROMPT_V1 = (
    "Use reference video 1 as the motion reference. Render one plain matte-white, "
    "faceless human mannequin on a clean neutral grey background, with a fixed camera. "
    "Preserve the reference person's visible body proportions, left/right limbs, "
    "joint trajectories, movement direction, timing, pauses and original imperfections. "
    "Replay only the observed motion in its original order and at the original speed. "
    "Do not improve the tennis technique, add a swing, change the action, mirror the person, "
    "invent ball contact, add text or move the camera. Keep the whole body visible. "
    "This is a visual recreation of the supplied motion, not a coaching demonstration."
)
MAX_INPUT_BYTES = 40 * 1024 * 1024  # Base64 plus JSON remains below the 64 MB request cap.
MAX_OUTPUT_BYTES = 64 * 1024 * 1024
_TASK_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


class ProviderError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool = False,
                 automatic_retry: bool = False, submission_unknown: bool = False):
        super().__init__(message)
        self.code = code
        self.public_message = message
        self.retryable = retryable
        self.automatic_retry = automatic_retry
        self.submission_unknown = submission_unknown


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def request_prompt(request: dict) -> str:
    snapshot = request.get("prompt")
    if isinstance(snapshot, str) and 1 <= len(snapshot) <= 7000:
        return snapshot
    # Explicit old-version compatibility; never silently rewrite a queued
    # request using whatever prompt the newly deployed worker happens to use.
    if "prompt" not in request and request.get("prompt_version") == "white-mannequin-motion-v1":
        return LEGACY_PROMPT_V1
    raise ProviderError("prompt_snapshot_missing", "该生成任务缺少可追溯的提示版本，请创建新请求。")


def build_generation_payload(reference_video: Path, *, model: str = "MiniMax-H3",
                             resolution: str = "768P", duration: int = 6, prompt: str = PROMPT) -> dict:
    if model != "MiniMax-H3" or resolution != "768P" or duration != 6:
        raise ProviderError("unsupported_generation_config", "当前只支持 H3 / 768P / 6 秒。")
    if not isinstance(prompt, str) or not 1 <= len(prompt) <= 7000:
        raise ProviderError("invalid_generation_prompt", "生成请求的提示版本无效。")
    path = Path(reference_video)
    try:
        size = path.stat().st_size
        if path.suffix.lower() != ".mp4" or not 8 <= size <= MAX_INPUT_BYTES:
            raise ProviderError("reference_video_invalid", "参考视频格式或大小不符合要求。")
        with path.open("rb") as stream:
            data = stream.read(MAX_INPUT_BYTES + 1)
    except OSError:
        raise ProviderError("reference_video_unavailable", "参考视频暂不可读取，尚未提交服务商。") from None
    if len(data) != size or len(data) > MAX_INPUT_BYTES:
        raise ProviderError("reference_video_invalid", "参考视频格式或大小不符合要求。")
    if data[4:8] != b"ftyp":
        raise ProviderError("reference_video_invalid", "参考视频必须是经过校验的 MP4。")
    return {
        "model": model,
        "content": [
            {"type": "text", "text": prompt},
            {"type": "video_url", "video_url": {"url": "data:video/mp4;base64," + base64.b64encode(data).decode("ascii")},
             "role": "reference_video"},
        ],
        "resolution": resolution, "duration": duration, "ratio": "16:9",
    }


def _download_url(url: Any, *, allow_proxy_dns: bool = False) -> str:
    if not isinstance(url, str) or len(url) > 8192 or any(ord(ch) <= 32 for ch in url):
        raise ProviderError("invalid_output_url", "生成结果下载地址无效。")
    try:
        parsed = urllib.parse.urlsplit(url)
        port = parsed.port
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
                or port not in {None, 443} or "\\" in url):
            raise ValueError()
        addresses = socket.getaddrinfo(parsed.hostname, port or 443, type=socket.SOCK_STREAM)
        def permitted_address(value: str) -> bool:
            address = ipaddress.ip_address(value)
            if address.is_global:
                return True
            # Opt-in support for a local proxy's fake-IP DNS. This exception is
            # bounded to the observed official media hostname and benchmark CIDRs.
            if allow_proxy_dns and parsed.hostname == "video-product.cdn.minimax.io":
                networks = (ipaddress.ip_network("198.18.0.0/15"), ipaddress.ip_network("2001:2::/48"))
                return any(address.version == network.version and address in network for network in networks)
            return False
        if not addresses or any(not permitted_address(item[4][0]) for item in addresses):
            raise ValueError()
    except OSError:
        raise ProviderError("output_dns_unavailable", "生成视频下载地址暂时无法解析。", retryable=True, automatic_retry=True) from None
    except ValueError:
        raise ProviderError("invalid_output_url", "生成结果下载地址不可用。") from None
    return url


class MiniMaxVideoProvider:
    def __init__(self, *, api_key: str | None = None, base_url: str = "https://api.minimax.io",
                 timeout: float = 60.0, opener: Any = None, allow_proxy_dns: bool = False):
        key = api_key if api_key is not None else os.environ.get("MINIMAX_API_KEY")
        if not isinstance(key, str) or not key.strip():
            raise ProviderError("credentials_unavailable", "生成服务尚未配置可用凭据。", retryable=True)
        if base_url.rstrip("/") not in {"https://api.minimax.io", "https://api.minimaxi.com"}:
            raise ValueError("MiniMax API base must be an official HTTPS origin")
        self._api_key = key.strip()
        self.base_url = base_url.rstrip("/")
        self.timeout = min(max(float(timeout), 1.0), 120.0)
        self._opener = opener or urllib.request.build_opener(_NoRedirect())
        self.allow_proxy_dns = allow_proxy_dns is True

    def _json_request(self, method: str, path: str, payload: dict | None = None) -> dict:
        submitting = method == "POST"
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8") if payload is not None else None
        if body is not None and len(body) > 64 * 1024 * 1024:
            raise ProviderError("reference_request_too_large", "参考视频请求超过大小限制。")
        request = urllib.request.Request(self.base_url + path, data=body, method=method,
                                        headers={"Authorization": "Bearer " + self._api_key, "Content-Type": "application/json"})
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                raw = response.read(1024 * 1024 + 1)
                if len(raw) > 1024 * 1024:
                    raise ValueError("oversized response")
                result = json.loads(raw)
                if not isinstance(result, dict) or result.get("type") == "error":
                    raise ValueError("invalid response")
                return result
        except urllib.error.HTTPError as exc:
            status = exc.code
            exc.close()
            if status in {401, 403}:
                raise ProviderError("provider_authentication_failed", "生成服务凭据校验未通过。", retryable=True) from None
            if status == 402:
                raise ProviderError("provider_balance_required", "生成服务额度不足。", retryable=True) from None
            if status == 429:
                raise ProviderError("provider_rate_limited", "生成服务繁忙，将按有限重试策略处理。",
                                    retryable=True, automatic_retry=True) from None
            if status in {400, 404, 422}:
                raise ProviderError("provider_request_rejected", "生成服务未接受该请求。") from None
            raise ProviderError("submission_outcome_unknown" if submitting else "provider_temporarily_unavailable",
                                "提交结果尚未确认，不会自动重复提交。" if submitting else "暂时无法查询生成任务。",
                                retryable=not submitting, automatic_retry=not submitting,
                                submission_unknown=submitting) from None
        except (OSError, ValueError, urllib.error.URLError, http.client.HTTPException):
            raise ProviderError("submission_outcome_unknown" if submitting else "provider_temporarily_unavailable",
                                "提交结果尚未确认，不会自动重复提交。" if submitting else "暂时无法查询生成任务。",
                                retryable=not submitting, automatic_retry=not submitting,
                                submission_unknown=submitting) from None

    def submit(self, reference_video: Path, *, model: str = "MiniMax-H3",
               resolution: str = "768P", duration: int = 6, prompt: str = PROMPT) -> str:
        payload = build_generation_payload(reference_video, model=model, resolution=resolution, duration=duration, prompt=prompt)
        result = self._json_request("POST", "/v2/video_generation", payload)
        task_id = result.get("task_id")
        if not isinstance(task_id, str) or not _TASK_ID.fullmatch(task_id):
            raise ProviderError("submission_outcome_unknown", "提交结果缺少可追踪任务编号，不会自动重复提交。",
                                submission_unknown=True)
        return task_id

    def query(self, task_id: str) -> dict:
        if not isinstance(task_id, str) or not _TASK_ID.fullmatch(task_id):
            raise ProviderError("invalid_provider_task", "生成任务编号无效。")
        result = self._json_request("GET", "/v2/query/video_generation/" + task_id)
        task = result.get("task")
        if not isinstance(task, dict) or str(task.get("id")) != task_id:
            raise ProviderError("invalid_provider_response", "查询结果无法与当前任务对应。", retryable=True, automatic_retry=True)
        status = task.get("status")
        if status not in {"queued", "running", "succeeded", "failed", "cancelled"}:
            raise ProviderError("invalid_provider_response", "生成任务状态暂不可识别。", retryable=True, automatic_retry=True)
        content = task.get("content")
        url = content.get("url") if isinstance(content, dict) else None
        if status == "succeeded" and (not isinstance(url, str) or not url):
            raise ProviderError("invalid_provider_response", "生成任务尚未返回视频下载地址。", retryable=True, automatic_retry=True)
        return {"status": status, "download_url": url if status == "succeeded" else None}

    def download(self, url: str, output_path: Path, *, max_bytes: int = MAX_OUTPUT_BYTES) -> None:
        target = Path(output_path)
        try:
            for _ in range(4):
                safe_url = _download_url(url, allow_proxy_dns=self.allow_proxy_dns)
                # No credential or API headers are ever sent to media hosts.
                request = urllib.request.Request(safe_url, headers={"Accept": "video/mp4"})
                try:
                    response = self._opener.open(request, timeout=self.timeout)
                    break
                except urllib.error.HTTPError as exc:
                    if exc.code in {301, 302, 303, 307, 308} and exc.headers.get("Location"):
                        url = urllib.parse.urljoin(safe_url, exc.headers["Location"])
                        exc.close()
                        continue
                    exc.close()
                    raise
            else:
                raise ProviderError("output_redirect_limit", "生成视频下载跳转次数过多。")
            target.parent.mkdir(parents=True, exist_ok=True)
            with response:
                length = response.headers.get("Content-Length")
                expected_length = int(length) if length is not None else None
                if expected_length is not None and (expected_length < 0 or expected_length > max_bytes):
                    raise ProviderError("output_too_large", "生成视频超过下载大小限制。")
                total = 0
                deadline = time.monotonic() + max(60.0, self.timeout * 2)
                with target.open("wb") as stream:
                    while block := response.read(1024 * 1024):
                        if time.monotonic() > deadline:
                            raise ProviderError("output_download_failed", "生成视频下载超过时限。", retryable=True, automatic_retry=True)
                        total += len(block)
                        if total > max_bytes:
                            raise ProviderError("output_too_large", "生成视频超过下载大小限制。")
                        stream.write(block)
                if expected_length is not None and total != expected_length:
                    raise ProviderError("output_download_incomplete", "生成视频下载不完整，将重新查询原任务后重试下载。",
                                        retryable=True, automatic_retry=True)
            with target.open("rb") as stream:
                header = stream.read(12)
            if len(header) < 12 or header[4:8] != b"ftyp":
                raise ProviderError("invalid_output_video", "生成结果不是有效 MP4 视频。")
        except ProviderError:
            target.unlink(missing_ok=True)
            raise
        except (OSError, ValueError, urllib.error.URLError, http.client.HTTPException):
            target.unlink(missing_ok=True)
            raise ProviderError("output_download_failed", "生成视频下载暂时失败。", retryable=True, automatic_retry=True) from None
