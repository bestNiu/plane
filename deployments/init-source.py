#!/usr/bin/env python3
"""Generate private deployment env files without putting passwords in arguments."""
import argparse
import getpass
import os
from pathlib import Path
import secrets
from string import Template
from urllib.parse import quote, urlparse

ROOT = Path(__file__).resolve().parent.parent


def generate(args, password):
    if not password:
        raise ValueError("Database password must not be empty")
    base_url = args.base_url.rstrip("/")
    parsed = urlparse(base_url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("Base URL must be an HTTP(S) URL")
    targets = {
        "api": ROOT / "apps/api/.env",
        "runtime": ROOT / ".deploy/runtime.env",
        "live": ROOT / ".deploy/live.env",
        "relay": ROOT / ".deploy/relay.env",
    }
    # Never replace existing credentials or signing keys by accident.
    existing = [str(p.relative_to(ROOT)) for p in targets.values() if p.exists()]
    if existing:
        raise FileExistsError("Configuration already exists: " + ", ".join(existing))
    values = {
        "BASE_URL": base_url,
        "DB_HOST": args.db_host,
        "DB_NAME": args.db_name,
        "DB_USER": args.db_user,
        "DB_PASSWORD": password,
        "DATABASE_URL": (
            f"postgresql://{quote(args.db_user, safe='')}:{quote(password, safe='')}"
            f"@pg-egress:15432/{quote(args.db_name, safe='')}?sslmode={args.sslmode}"
        ),
        "DJANGO_SECRET_KEY": secrets.token_hex(32),
        "LIVE_SECRET_KEY": secrets.token_hex(32),
        "MQ_PASSWORD": secrets.token_hex(24),
        "S3_ACCESS_KEY": secrets.token_hex(12),
        "S3_SECRET_KEY": secrets.token_hex(32),
    }
    rendered = {}
    for name, path in targets.items():
        template = (ROOT / f"deployments/env/{name}.env.example").read_text()
        rows = []
        for row in template.splitlines():
            if not row or row.startswith("#"):
                continue
            key, value = row.split("=", 1)
            value = Template(value).substitute(values)
            if "\n" in value or "\r" in value:
                raise ValueError("Environment values cannot contain newlines")
            # Single quotes preserve literal dollars in Compose env files.
            value = value.replace("\\", "\\\\").replace("'", "\\'")
            rows.append(f"{key}='{value}'")
        rendered[path] = "\n".join(rows) + "\n"
    runtime = ROOT / ".deploy"
    runtime.mkdir(mode=0o700, exist_ok=True)
    runtime.chmod(0o700)
    for path, content in rendered.items():
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as stream:
            stream.write(content)
        print(f"Created {path.relative_to(ROOT)} (mode 600)")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-host", required=True)
    parser.add_argument("--db-name", required=True)
    parser.add_argument("--db-user", required=True)
    parser.add_argument("--base-url", default="http://192.168.0.238:8090")
    parser.add_argument("--sslmode", choices=("disable", "require", "verify-full"), default="require")
    args = parser.parse_args()
    try:
        generate(args, getpass.getpass("PostgreSQL password: "))
    except (ValueError, FileExistsError) as error:
        parser.exit(1, f"Error: {error}\n")


if __name__ == "__main__":
    main()
