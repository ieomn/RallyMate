# syntax=docker/dockerfile:1.7
# Ubuntu 22.04 ships Python 3.10, the tested compatibility target for the
# pinned MMPose/MMCV/MMDet runtime. The host CUDA driver remains independent.
FROM nvidia/cuda:12.8.1-cudnn-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        curl ffmpeg libgl1 libglib2.0-0 build-essential python3.10 python3.10-dev \
        python3-pip python3.10-venv \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Layer order matters: heavy, rarely-changing dependencies (toolchain, CUDA
# torch wheels, then the project's resolved extras) are installed before any
# application source is copied, so a source-only change reuses these layers.
# MMPose's legacy chumpy metadata imports pip; build against this environment.
RUN python3.10 -m pip install --upgrade pip \
    && python3.10 -m pip install --break-system-packages \
        setuptools==69.5.1 wheel Cython numpy==1.26.4 tomli==2.0.1 \
    && python3.10 -m pip install --break-system-packages \
        --index-url https://download.pytorch.org/whl/cu128 torch==2.11.0 torchvision==0.26.0

COPY pyproject.toml /app/
# Resolve the existing extras from pyproject only (no src yet). Python 3.10
# has no tomllib, hence the pinned tomli above.
RUN python3.10 - <<'PY'
import subprocess
import sys
import tomli
with open("pyproject.toml", "rb") as source:
    project = tomli.load(source)["project"]
requirements = project["dependencies"] + [
    requirement for extra in ("service", "training", "rtmpose")
    for requirement in project["optional-dependencies"][extra]
]
subprocess.run([sys.executable, "-m", "pip", "install", "--break-system-packages",
                "--no-build-isolation", *requirements], check=True)
PY

COPY README.md /app/
COPY src /app/src
COPY registry-lifecycle.json \
     metric-feasibility.json \
     metric-measurement-plans.json \
     metric-feasibility-pose-wave-v2.json \
     indicator-scoring-requirements-pose-wave-v1.json \
     metric-feasibility-pose-wave-v3.json \
     indicator-scoring-requirements-pose-wave-v2.json \
     /app/

# Dependencies are already present; this only registers the editable package
# (entry points such as rallymate-api / rallymate-worker, package data).
RUN python3.10 -m pip install --break-system-packages --no-deps --no-build-isolation -e .

RUN useradd --create-home --uid 10001 rallymate \
    && mkdir -p /app/service_data /app/models /var/lib/rallymate/gpu-state/locks \
    && chown -R rallymate:rallymate /app/service_data /app/models /var/lib/rallymate/gpu-state

USER rallymate
EXPOSE 8000
CMD ["rallymate-api", "--host", "0.0.0.0", "--port", "8000"]
