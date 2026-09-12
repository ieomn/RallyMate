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
COPY pyproject.toml README.md /app/
COPY src /app/src
COPY registry-lifecycle.json \
     metric-feasibility.json \
     metric-measurement-plans.json \
     metric-feasibility-pose-wave-v2.json \
     indicator-scoring-requirements-pose-wave-v1.json \
     /app/

RUN python3.10 -m pip install --upgrade pip \
    && python3.10 -m pip install --break-system-packages \
        --index-url https://download.pytorch.org/whl/cu128 torch==2.11.0 \
    && python3.10 -m pip install --break-system-packages -e ".[service,training,rtmpose]"

RUN useradd --create-home --uid 10001 rallymate \
    && mkdir -p /app/service_data /app/models \
    && chown -R rallymate:rallymate /app/service_data /app/models

USER rallymate
EXPOSE 8000
CMD ["rallymate-api", "--host", "0.0.0.0", "--port", "8000"]
