"""Generate private Linux Compose credentials without printing or overwriting them."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import secrets


def prepare(directory: Path, public_host: str, admin_email: str) -> list[Path]:
    if not re.fullmatch(r"(?=.{1,253}$)[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?", public_host):
        raise ValueError("public-host must be a hostname without scheme, path or port")
    if not re.fullmatch(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", admin_email):
        raise ValueError("admin-email must be an email address")
    directory = directory.resolve()
    template = (Path(__file__).resolve().parent / ".env.example").read_text(encoding="utf-8")
    password = secrets.token_urlsafe(36)
    values = {
        "PUBLIC_HOST": public_host, "POSTGRES_PASSWORD": password,
        "DATABASE_URL": f"postgresql+psycopg://rallymate:{password}@postgres:5432/rallymate",
        "JWT_SECRET": secrets.token_urlsafe(48), "RALLYMATE_API_KEY": secrets.token_urlsafe(48),
        "ADMIN_EMAIL": admin_email, "ADMIN_PASSWORD": "Rm9!" + secrets.token_urlsafe(30),
    }
    for key, value in values.items():
        template, count = re.subn(r"(?m)^" + re.escape(key) + r"=.*$", lambda _: f"{key}={value}", template)
        if count != 1:
            raise ValueError(f"deployment template must define {key} exactly once")
    outputs = {
        directory / ".env": (template, 0o600),
        directory / "secrets" / "metrics_token": (secrets.token_urlsafe(48) + "\n", 0o444),
        directory / "secrets" / "grafana_admin_password": (secrets.token_urlsafe(36) + "\n", 0o444),
    }
    if any(path.exists() or path.is_symlink() for path in outputs):
        raise ValueError("existing configuration or secret found; no files were overwritten")
    secret_dir = directory / "secrets"
    if secret_dir.is_symlink():
        raise ValueError("secret directory must not be a symlink")
    secret_dir.mkdir(parents=True, mode=0o700, exist_ok=True)
    secret_dir.chmod(0o700)
    created = []
    try:
        for path, (content, mode) in outputs.items():
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, mode)
            created.append(path)
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(content)
            path.chmod(mode)
    except Exception:
        for path in reversed(created):
            path.chmod(0o600)
            path.unlink()
        raise
    return created


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--public-host", required=True)
    parser.add_argument("--admin-email", required=True)
    parser.add_argument("--directory", type=Path, default=Path(__file__).resolve().parent)
    args = parser.parse_args()
    try:
        created = prepare(args.directory, args.public_host, args.admin_email)
    except (ValueError, OSError) as error:
        parser.exit(2, f"Configuration generation stopped: {type(error).__name__}. Check input and existing private files.\n")
    print("Created private configuration files:")
    for path in created:
        print(path)
    print("Read the initial administrator password inside .env. Set model/license and TLS paths before startup.")


if __name__ == "__main__":
    main()
