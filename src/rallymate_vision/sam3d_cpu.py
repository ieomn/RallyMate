"""CPU pool policy for the isolated SAM worker, without importing model packages.

The environment must be prepared before creating the process: NumPy and native
thread runtimes can initialize before the model constructor runs. Runtime pool
configuration still reads back the actual values before any model work starts.
"""
from __future__ import annotations

from collections.abc import Mapping
from threading import Lock


SAM_CPU_THREADS = "RALLYMATE_SAM3D_CPU_THREADS"
SAM_INTEROP_THREADS = "RALLYMATE_SAM3D_INTEROP_THREADS"
SAM_WAIT_MODE = "RALLYMATE_SAM3D_WAIT_MODE"
_CONFIGURATION_LOCK = Lock()


class Sam3DCpuConfigurationError(RuntimeError):
    """The requested SAM CPU limits could not be validated or applied."""


def _positive_integer(env: Mapping[str, str], name: str, default: str) -> int:
    raw = env.get(name, default)
    try:
        if not isinstance(raw, str):
            raise ValueError("environment values must be strings")
        value = int(raw)
        if value < 1:
            raise ValueError("thread count must be positive")
    except (ValueError, TypeError) as exc:
        raise Sam3DCpuConfigurationError(
            f"{name} must be an integer >= 1; got {raw!r}"
        ) from exc
    return value


def _requested_pools(env: Mapping[str, str]) -> tuple[int, int]:
    intraop_name = SAM_CPU_THREADS if SAM_CPU_THREADS in env else "RALLYMATE_CPU_THREADS"
    return (
        _positive_integer(env, intraop_name, "4"),
        _positive_integer(env, SAM_INTEROP_THREADS, "1"),
    )


def prepare_sam_worker_environment(env: Mapping[str, str]) -> dict[str, str]:
    """Return isolated child settings; never mutate the parent's environment."""
    intraop, interop = _requested_pools(env)
    child = dict(env)
    child[SAM_CPU_THREADS] = str(intraop)
    child[SAM_INTEROP_THREADS] = str(interop)
    # OMP_THREAD_LIMIT and NUMEXPR_MAX_THREADS also need replacement: inherited
    # hard limits can otherwise defeat a correct NUM_THREADS setting.
    for name in (
        "OMP_NUM_THREADS", "OMP_THREAD_LIMIT", "MKL_NUM_THREADS",
        "OPENBLAS_NUM_THREADS", "NUMEXPR_NUM_THREADS", "NUMEXPR_MAX_THREADS",
    ):
        child[name] = str(intraop)
    child["OPENCV_FOR_THREADS_NUM"] = "1"
    wait_mode = env.get(SAM_WAIT_MODE, "inherit")
    if wait_mode == "runtime_default":
        # Restore the measured native policy in this child only. The parent
        # can remain passive without imposing its policy on SAM.
        child.pop("OMP_WAIT_POLICY", None)
        child.pop("KMP_BLOCKTIME", None)
    elif wait_mode == "passive":
        child["OMP_WAIT_POLICY"], child["KMP_BLOCKTIME"] = "PASSIVE", "0"
    elif wait_mode == "inherit":
        child.setdefault("OMP_WAIT_POLICY", "PASSIVE")
        child.setdefault("KMP_BLOCKTIME", "0")
    else:
        raise Sam3DCpuConfigurationError(
            f"{SAM_WAIT_MODE} must be inherit, passive, or runtime_default; got {wait_mode!r}"
        )
    return child


def _apply_pool(name, requested, getter, setter) -> int:
    def observe() -> int:
        try:
            actual = getter()
            if isinstance(actual, bool) or not isinstance(actual, int) or actual < 1:
                raise ValueError(f"invalid runtime thread count {actual!r}")
            return actual
        except Exception as exc:
            raise Sam3DCpuConfigurationError(
                f"failed to read {name} CPU threads before SAM model loading "
                f"(requested={requested}): {exc}"
            ) from exc

    current = observe()
    if current != requested:
        try:
            setter(requested)
        except Exception as exc:
            raise Sam3DCpuConfigurationError(
                f"failed to set {name} CPU threads before SAM model loading "
                f"(requested={requested}, current={current}): {exc}"
            ) from exc
    actual = observe()
    if actual != requested:
        raise Sam3DCpuConfigurationError(
            f"{name} CPU limit was not applied before SAM model loading: "
            f"requested={requested}, actual={actual}"
        )
    return actual


def configure_sam_cpu_pools(torch, cv2, env: Mapping[str, str]) -> dict[str, int]:
    """Apply and verify CPU pools before model loading; CUDA is never touched."""
    intraop, interop = _requested_pools(env)
    with _CONFIGURATION_LOCK:
        # Inter-op can only be changed once before parallel work. Reading it
        # first makes repeated identical initialization safe.
        actual_interop = _apply_pool(
            "Torch inter-op", interop,
            torch.get_num_interop_threads, torch.set_num_interop_threads,
        )
        actual_intraop = _apply_pool(
            "Torch intra-op", intraop, torch.get_num_threads, torch.set_num_threads,
        )
        actual_opencv = _apply_pool("OpenCV", 1, cv2.getNumThreads, cv2.setNumThreads)
    return {
        "requested_intraop": intraop,
        "requested_interop": interop,
        "torch_intraop": actual_intraop,
        "torch_interop": actual_interop,
        "opencv": actual_opencv,
    }
