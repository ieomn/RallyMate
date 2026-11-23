"""Modular route registrars sharing typed application state."""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from threading import Event, Lock, RLock
from types import ModuleType
from typing import Any, Callable


@dataclass
class ApiRouteContext:
    """Shared live dependencies passed into route registrars.

    ``db`` and ``uploads`` are rebound in place when ``install_account_system``
    installs the user-scoped wrappers, so every registered route reads the
    account-scoped storage transparently. ``api_mod`` points to
    ``rallymate_service.api`` so existing test patches on module attributes
    continue to intercept calls cleanly.
    """

    api_mod: ModuleType
    service_settings: Any
    db: Any
    uploads: Any
    deployment_identity: Any
    admission_lock: RLock
    maintenance: Event
    authorize: Callable[..., None]
    execution_status: Callable[[], dict]
    execution_admits: Callable[[dict], bool]
    executor_shutdown: Callable[[], dict] | None
    heavy_report_slot: Callable[[], Any]
    summary_with_actions: Callable[[dict], dict]
    report_records: Callable[..., Any]
    report_delivery_cache: Any
    technical_reviews: Any
    trajectory_cache: OrderedDict
    trajectory_cache_lock: Lock
    live_trajectory_cache: Any
    pose_cache: OrderedDict
    pose_cache_lock: Lock
