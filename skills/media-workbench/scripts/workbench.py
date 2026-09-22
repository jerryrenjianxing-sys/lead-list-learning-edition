"""Standard-library client. Every mutation has a recoverable request identity."""

from __future__ import annotations
import argparse
import hashlib
import json
import os
import sys
import time
import uuid
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, ProxyHandler, HTTPRedirectHandler


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def output(value, *, error=False):
    stream = sys.stderr if error else sys.stdout
    print(json.dumps(value, ensure_ascii=False), file=stream, flush=True)


def default_instance():
    return Path(
        os.environ.get("MEDIAWORKBENCH_INSTANCE")
        or (
            Path(os.environ.get("LOCALAPPDATA", Path.home() / ".local/share"))
            / "MediaWorkbench"
            / "instance.json"
        )
    )


class Client:
    def __init__(self, instance=None):
        self.instance_path = Path(instance or default_instance())
        self.instance = json.loads(self.instance_path.read_text(encoding="utf-8-sig"))
        self.base = self.instance["base_url"].rstrip("/")
        url = urlsplit(self.base)
        if (
            url.scheme != "http"
            or url.hostname not in ("127.0.0.1", "localhost", "::1")
            or url.username
            or url.password
        ):
            raise ValueError("Discovery must identify a local HTTP service")
        if (
            self.instance.get("product") != "MediaWorkbench"
            or self.instance.get("api_version") != 1
        ):
            raise ValueError("This client requires MediaWorkbench API v1")
        self.opener = build_opener(ProxyHandler({}), NoRedirect())

    def send(self, method, path, data=None, headers=None, timeout=60):
        if not path.startswith("/") or path.startswith("//") or urlsplit(path).scheme:
            raise ValueError("Use a relative API path beginning with /")
        request = Request(
            self.base + path,
            data=data,
            method=method,
            headers={
                "Authorization": "Bearer " + self.instance["token"],
                **(headers or {}),
            },
        )
        with self.opener.open(request, timeout=timeout) as response:
            return response.read()

    def check(self):
        health = json.loads(self.send("GET", "/api/v1/health", timeout=5))
        if health.get("product") != "MediaWorkbench" or health.get("api_version") != 1:
            raise ValueError(
                "The local address is not a compatible MediaWorkbench instance"
            )
        return {
            **health,
            "base_url": self.base,
            "capabilities": json.loads(
                self.send("GET", "/api/v1/capabilities", timeout=5)
            ),
        }

    def request(
        self, method, path, data=None, content_type="application/json", request_id=None
    ):
        method = method.upper()
        mutating = method not in ("GET", "HEAD")
        headers = {"Content-Type": content_type}
        journal = None
        if mutating:
            request_id = request_id or uuid.uuid4().hex
            if any(
                c
                not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_."
                for c in request_id
            ):
                raise ValueError(
                    "Request ID must contain letters, numbers, dots, underscores or hyphens"
                )
            headers["Idempotency-Key"] = request_id
            folder = self.instance_path.parent / "client-receipts"
            folder.mkdir(parents=True, exist_ok=True)
            journal = folder / (request_id + ".json")
            fingerprint = hashlib.sha256(
                method.encode() + path.encode() + (data or b"")
            ).hexdigest()
            if journal.exists():
                previous = json.loads(journal.read_text(encoding="utf-8"))
                if previous["fingerprint"] != fingerprint:
                    raise ValueError("Request ID already used for a different request")
            pending = {
                "request_id": request_id,
                "method": method,
                "path": path,
                "fingerprint": fingerprint,
                "state": "pending",
            }
            journal.write_text(json.dumps(pending), encoding="utf-8")
            output({"request_id": request_id, "receipt": str(journal)}, error=True)
        for attempt in range(3):
            try:
                raw = self.send(method, path, data, headers)
                result = json.loads(raw) if raw else {}
                if journal:
                    journal.write_text(
                        json.dumps(
                            {**pending, "state": "completed", "response": result},
                            ensure_ascii=False,
                        ),
                        encoding="utf-8",
                    )
                return result
            except HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")
                raise RuntimeError(f"HTTP {exc.code}: {detail}") from exc
            except (URLError, TimeoutError, OSError):
                if mutating:
                    try:
                        receipt = json.loads(
                            self.send(
                                "GET", "/api/v1/requests/" + request_id, timeout=5
                            )
                        )
                        result = receipt["response"]
                        if journal:
                            journal.write_text(
                                json.dumps(
                                    {
                                        **pending,
                                        "state": "completed",
                                        "response": result,
                                    },
                                    ensure_ascii=False,
                                ),
                                encoding="utf-8",
                            )
                        return result
                    except (URLError, HTTPError, TimeoutError, OSError):
                        pass
                if attempt == 2:
                    raise
                time.sleep(0.5 * (attempt + 1))


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--instance")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("check")
    api = commands.add_parser("api")
    api.add_argument("method")
    api.add_argument("path")
    api.add_argument("--json-file")
    api.add_argument("--json")
    api.add_argument("--request-id")
    receipt = commands.add_parser("receipt")
    receipt.add_argument("request_id")
    download = commands.add_parser("download")
    download.add_argument("path")
    download.add_argument("destination")
    upload = commands.add_parser("upload")
    upload.add_argument("path")
    upload.add_argument("file")
    upload.add_argument("--request-id")
    args = parser.parse_args()
    try:
        client = Client(args.instance)
        health = client.check()
        if args.command == "check":
            result = health
        elif args.command == "receipt":
            result = json.loads(
                client.send("GET", "/api/v1/requests/" + args.request_id)
            )
        elif args.command == "download":
            content = client.send("GET", args.path)
            target = Path(args.destination)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
            result = {
                "file": str(target.resolve()),
                "size": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
            }
        elif args.command == "upload":
            file = Path(args.file)
            content = file.read_bytes()
            # Deterministic multipart framing lets the same request ID replay exactly.
            boundary = "workbench-" + hashlib.sha256(content).hexdigest()[:32]
            name = file.name.replace('"', "_").replace("\r", "_").replace("\n", "_")
            body = (
                (
                    f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{name}"\r\nContent-Type: application/octet-stream\r\n\r\n'
                ).encode()
                + content
                + f"\r\n--{boundary}--\r\n".encode()
            )
            result = client.request(
                "POST",
                args.path,
                body,
                "multipart/form-data; boundary=" + boundary,
                args.request_id,
            )
        else:
            body = (
                json.loads(Path(args.json_file).read_text(encoding="utf-8-sig"))
                if args.json_file
                else json.loads(args.json)
                if args.json
                else None
            )
            result = client.request(
                args.method,
                args.path,
                json.dumps(body, ensure_ascii=False).encode("utf-8")
                if body is not None
                else None,
                request_id=args.request_id,
            )
        output(result)
    except Exception as exc:
        output(
            {
                "ok": False,
                "error": str(exc),
                "next_step": "Start MediaWorkbench and check the local instance, or inspect the reported request receipt.",
            }
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
