# SAM cloud environment bootstrap

`bootstrap-sam-environment.sh` prepares the project at
`/root/autodl-tmp/rallymate`. It does not create `runtime.env`, start services,
change system Supervisor jobs, or open the Cloudflare tunnel.

```bash
cd /root/autodl-tmp/rallymate
bash deploy/autodl/bootstrap-sam-environment.sh
```

The script creates a timestamped `.venvs/sam-*` environment and points `.venv`
to it. It reuses the AutoDL image's Python 3.12.3, PyTorch 2.8.0+cu128 and
torchvision 0.23.0+cu128 through `--system-site-packages`. New dependencies,
downloads, temporary files, package caches, and installation logs remain under
the project directory. The system Python installation is not modified.

The first run installs the project's service and RTMPose extras plus the SAM
bbox inference requirements from `deploy/autodl/requirements-sam-core.in`.
Keep this small dependency recipe and `requirements-constraints.txt` in the
checkout and deployment source manifest; installation does not depend on an
ignored requirements file under `runtime/`. The recipe preserves the previously
used package requirements; it does not include model weights or third-party
source trees. The existing AutoDL constraints pin NumPy to 1.26.4;
`xtcocotools` is installed without build isolation. PyAV is fixed
at 16.0.1 so the cloud and benchmark source decoders can use the same version.
`imageio-ffmpeg` provides the FFmpeg binary where the image has no system FFmpeg;
the script also links it as `tools/bin/ffmpeg`.

Node 22.23.2 and cloudflared 2026.9.1 are downloaded from their official URLs,
checked against the SHA-256 values recorded in the deployment README, and
unpacked inside `tools/`. The front end is installed from `package-lock.json`
with `npm ci` and built for production.

To retry only one part:

```bash
bash deploy/autodl/bootstrap-sam-environment.sh python
bash deploy/autodl/bootstrap-sam-environment.sh frontend
```

The default package index is official PyPI. An explicit trusted mirror can be
selected when the cloud route to PyPI is slow; this changes Python package
transport only, not model weight sources or package version constraints:

```bash
RALLYMATE_PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple \
  bash deploy/autodl/bootstrap-sam-environment.sh python
```

Review `service_data/ops/install/` after installation:

- `bootstrap.exit`: `running` while active, then the exit code.
- `python-install.log`, `frontend-install.log`: component output.
- `python-versions.json`, `python-freeze.txt`: actual installed versions.
- `pip-check.txt`: dependency consistency, including packages inherited from
  the base image. Read this file even if the installation returned success.

Installation and import checks do not validate SAM inference. Official model
hash verification, real-frame GPU loading, video measurements, and public URL
acceptance are separate deployment steps. Keep the runtime cache/config
environment variables in the service launcher as well; variables exported by
the bootstrap apply only to installation.

The current AutoDL image provides the NVIDIA EGL vendor library but omits the
generic `libEGL.so` loader. For diagnostic renderer imports, install the small
Ubuntu Jammy loader into the project and expose it to that process:

```bash
bash deploy/autodl/install-local-egl.sh
export LD_LIBRARY_PATH="/root/autodl-tmp/rallymate/tools/egl/usr/lib/x86_64-linux-gnu:${LD_LIBRARY_PATH:-}"
```

This verifies the [Ubuntu package SHA-256](https://packages.ubuntu.com/jammy/amd64/libegl1/download)
and extracts the package without system installation. SAM's core bbox
inference module imported before this optional renderer dependency was added.

For a Python-environment rollback, stop this project's complete private
Supervisor with `deploy/autodl/manage.sh stop`, preserve the old `.venv`
symlink target, and point `.venv` at the previously validated environment.
After checking its executables, start it with `deploy/autodl/manage.sh start`
and retrieve the current address with `deploy/autodl/manage.sh url`. This full
stop also stops cloudflared, so the Quick Tunnel hostname may change. The
platform's unrelated Supervisor jobs are unaffected.

Selecting RTMPose instead of SAM is a model-configuration rollback; it does
not require changing `.venv` or stopping cloudflared. See `SAM_DEPLOYMENT.md`
for model selection and service operations. Do not delete prior environments
until their runs and reports no longer need them. Re-running the bootstrap
uses the current `.venv`; it does not rotate or replace an existing environment
automatically, and should not run against an environment serving active jobs.

## Verified environment, 2026-10-18

On NVIDIA RTX 6000D, the installation completed and `pip check` reported no
broken requirements. Nine environment checks passed: CUDA computation,
RTMPose imports using the project's existing MMCV Lite compatibility, SAM main
module, SAM renderer import, visualization helpers, PyAV import, imageio-ffmpeg
import, service CLI import, and the PyAV 16.0.1 pin. No model inference or
rendering result is implied by these import checks.

Additional versions were PyTorch Lightning 2.6.6, timm 1.0.30,
huggingface-hub 2.2.0, Supervisor 4.2.5, and imageio-ffmpeg 0.6.0 with its
FFmpeg 7.0.2 binary. Node and cloudflared matched the versions above. Frontend
`npm ci` installed 479 packages and the production build passed; Vite reported
the existing large-chunk advisory and static route-classification limitations.
The package-lock SHA-256 for that build was
`508a76b7da43dd0cf936b94b957d27bf6151c6cb6718cf437cfd12c20b0bef0c`.
