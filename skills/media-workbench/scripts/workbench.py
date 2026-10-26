"""Standard-library client. Every mutation has a recoverable request identity."""

from __future__ import annotations
import argparse
from contextlib import contextmanager
import hashlib
import json
import os
import sys
import time
import uuid
import tempfile
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, ProxyHandler, HTTPRedirectHandler
from typing import Any, Literal, NotRequired, TypedDict


class ConnectionSummary(TypedDict):
    operations: list[str]
    exports: list[str]
    analysis: str
    platforms: NotRequired[list[str]]
    live_validation: NotRequired[str]


class ConnectionResult(TypedDict):
    product: str
    display_name: str
    version: str
    api_version: int
    status: str
    connection: Literal["ready"]
    base_url: str
    workspace_id: str
    summary: ConnectionSummary
    help: dict[str, str]
    instance_id: NotRequired[str]
    capability_revision: NotRequired[str]
    connection_mode: NotRequired[Literal["legacy"]]


class CheckResult(ConnectionResult):
    capabilities: dict[str, Any]


class ErrorResult(TypedDict):
    ok: Literal[False]
    code: str
    error: str
    retryable: bool
    next_action: str
    next_step: str
    request_id: NotRequired[str]


class JournalRecord(TypedDict):
    request_id: str
    method: str
    path: str
    fingerprint: str
    state: Literal["pending", "completed"]
    workspace_id: NotRequired[str | None]
    content_type: NotRequired[str]
    response: NotRequired[Any]


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def output(value, *, error=False):
    stream = sys.stderr if error else sys.stdout
    print(json.dumps(value, ensure_ascii=False), file=stream, flush=True)


def default_instance() -> Path:
    return Path(
        os.environ.get("MEDIAWORKBENCH_INSTANCE")
        or (
            Path(os.environ.get("LOCALAPPDATA", Path.home() / ".local/share"))
            / "MediaWorkbench"
            / "instance.json"
        )
    )


class ClientError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool = False,
                 next_action: str = "检查软件内的连接状态后重试。", request_id: str | None = None) -> None:
        super().__init__(message)
        self.code, self.retryable, self.next_action = code, retryable, next_action
        self.request_id = request_id

    def as_dict(self) -> ErrorResult:
        result: ErrorResult = {"ok": False, "code": self.code, "error": str(self),
                  "retryable": self.retryable, "next_action": self.next_action,
                  "next_step": self.next_action}
        if self.request_id:
            result["request_id"] = self.request_id
        return result


def workspace_key(directory: str | os.PathLike[str]) -> str:
    return hashlib.sha256(os.path.normcase(os.path.realpath(directory)).encode("utf-8")).hexdigest()


@contextmanager
def journal_lock(path):
    """OS locks cover multiple Agent processes, not just threads in one client."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix(".lock").open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        deadline = time.monotonic() + 5
        while True:
            try:
                if os.name == "nt":
                    import msvcrt
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as exc:
                if time.monotonic() >= deadline:
                    raise ClientError("JOURNAL_BUSY", "另一个客户端正在保存同一请求的回执。", retryable=True) from exc
                time.sleep(0.02)
        try:
            yield
        finally:
            if os.name == "nt":
                import msvcrt
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def atomic_journal(path: Path, value: JournalRecord) -> None:
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=path.name + ".", suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(value, handle, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def read_journal(path: Path, request_id: str) -> JournalRecord:
    try:
        result = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(result, dict):
            raise ValueError("not a journal object")
        return result
    except (OSError, ValueError) as exc:
        raise ClientError("JOURNAL_INVALID", "无法读取该请求的本机回执，未重新提交操作。",
                          next_action="保留原请求编号，使用 receipt 查询后台事实；不要新建重复任务。", request_id=request_id) from exc


class Client:
    def __init__(self, instance: str | os.PathLike[str] | None = None) -> None:
        self.instance_path = Path(instance or default_instance())
        self.opener = build_opener(ProxyHandler({}), NoRedirect())
        self._connection: ConnectionResult | None = None
        self._capabilities = None
        self._rediscovered = False
        self.instance = self._read_discovery()
        self.base = self.instance["base_url"].rstrip("/")
        self._workspace = self._discovery_workspace(self.instance)

    def _read_discovery(self):
        try:
            info = json.loads(self.instance_path.read_text(encoding="utf-8-sig"))
        except FileNotFoundError as exc:
            raise ClientError("APP_NOT_RUNNING", "没有找到本机软件实例。", retryable=True,
                              next_action="启动 Media Deep Researcher 后使用同一客户端重试。") from exc
        except (OSError, ValueError) as exc:
            raise ClientError("DISCOVERY_INVALID", "无法读取有效的本机实例信息。",
                              next_action="正常重启软件以重新发布实例信息，不要输出或手工编辑凭据。") from exc
        if not isinstance(info, dict):
            raise ClientError("DISCOVERY_INVALID", "本机实例信息格式不正确。")
        try:
            url = urlsplit(info.get("base_url", ""))
            port = url.port
        except (ValueError, TypeError) as exc:
            raise ClientError("DISCOVERY_INVALID", "本机实例地址格式不正确。") from exc
        if (
            url.scheme != "http"
            or url.hostname not in ("127.0.0.1", "localhost", "::1")
            or url.username
            or url.password
            or url.path not in ("", "/")
            or url.query or url.fragment or not port
        ):
            raise ClientError("DISCOVERY_INVALID", "实例必须指向本机 HTTP 地址。")
        if (
            info.get("product") != "MediaWorkbench"
            or info.get("api_version") != 1
        ):
            raise ClientError("PROTOCOL_UNSUPPORTED", "实例不是兼容的 MediaWorkbench API v1。",
                              next_action="使用该软件提供的匹配客户端，或升级软件。")
        if not isinstance(info.get("token"), str) or not info["token"]:
            raise ClientError("DISCOVERY_INVALID", "实例缺少本机访问凭据。")
        return info

    @staticmethod
    def _discovery_workspace(info):
        directory = info.get("data_directory")
        calculated = workspace_key(directory) if isinstance(directory, str) and directory else None
        declared = info.get("workspace_id")
        if declared and calculated and declared != calculated:
            raise ClientError("WORKSPACE_MISMATCH", "实例的数据目录身份不一致。")
        return declared or calculated

    def _rediscover(self):
        if self._rediscovered:
            return False
        self._rediscovered = True
        candidate = self._read_discovery()
        candidate_workspace = self._discovery_workspace(candidate)
        if self._workspace and candidate_workspace != self._workspace:
            raise ClientError("WORKSPACE_MISMATCH", "新的后台属于其他数据目录，已停止自动恢复。",
                              next_action="确认需要使用的数据目录后，启动新的客户端操作。")
        if not self._workspace and not candidate_workspace:
            # A legacy discovery without a workspace identity cannot safely move.
            if candidate["base_url"] != self.instance["base_url"]:
                raise ClientError("WORKSPACE_UNCONFIRMED", "无法确认新地址属于原数据目录。")
        self.instance = candidate
        self.base = candidate["base_url"].rstrip("/")
        self._workspace = self._workspace or candidate_workspace
        self._connection = None
        self._capabilities = None
        return True

    @staticmethod
    def _http_error(exc):
        if exc.code in (401, 403):
            return ClientError("AUTH_FAILED", "本机访问权限未确认。", retryable=True,
                               next_action="确认软件已正常运行，重新读取本机实例后重试；不要复制访问凭据。")
        if exc.code == 409:
            return ClientError("CONFLICT", "操作与已有请求或软件当前状态冲突。",
                               next_action="保留原请求编号，查看请求回执和软件状态；修改请求内容时换用新编号。")
        if exc.code == 404:
            return ClientError("NOT_FOUND", "请求的功能或对象不存在。",
                               next_action="查询当前软件的能力与对象编号，不要重复创建已有任务。")
        return ClientError("HTTP_ERROR", f"本机请求返回 HTTP {exc.code}。", retryable=exc.code >= 500,
                           next_action="检查请求参数和软件中的错误详情，保留原请求编号。")

    @staticmethod
    def _remaining(deadline):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ClientError("TIMEOUT", "连接超过等待预算。", retryable=True)
        return min(5.0, remaining)

    def _send(self, method, path, data=None, headers=None, timeout=60):
        if not path.startswith("/") or path.startswith("//") or urlsplit(path).scheme:
            raise ClientError("INVALID_REQUEST", "使用以 / 开头的相对接口路径。")
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

    def _validate_identity(self, health):
        if not isinstance(health, dict):
            raise ClientError("IDENTITY_MISMATCH", "本机响应缺少软件身份。")
        if health.get("product") != "MediaWorkbench" or health.get("api_version") != 1:
            raise ClientError("IDENTITY_MISMATCH", "本机响应不是兼容的 MediaWorkbench API v1。")
        if health.get("status") != "ok":
            raise ClientError("HOST_NOT_READY", "软件后台尚未就绪。", retryable=True,
                              next_action="稍候重试连接，或查看软件的启动状态。")
        if self.instance.get("version") and self.instance["version"] != health.get("version"):
            raise ClientError("VERSION_MISMATCH", "实例记录与正在运行的后台版本不一致。", retryable=True)

    def _connect_once(self, deadline: float) -> ConnectionResult:
        try:
            result = json.loads(self._send("GET", "/api/v1/agent/handshake", timeout=self._remaining(deadline)))
        except HTTPError as exc:
            if exc.code not in (404, 405):
                raise
            health = json.loads(self._send("GET", "/api/v1/health", timeout=self._remaining(deadline)))
            self._validate_identity(health)
            capabilities = json.loads(self._send("GET", "/api/v1/capabilities", timeout=self._remaining(deadline)))
            self._validate_identity(capabilities)
            settings = json.loads(self._send("GET", "/api/v1/settings", timeout=self._remaining(deadline)))
            if not isinstance(settings, dict) or not isinstance(settings.get("data_directory"), str):
                raise ClientError("WORKSPACE_UNCONFIRMED", "旧版后台未返回数据目录身份。")
            workspace = workspace_key(settings["data_directory"])
            if self._workspace and workspace != self._workspace:
                raise ClientError("WORKSPACE_MISMATCH", "旧版后台的数据目录与实例不一致。")
            self._workspace = workspace
            self._capabilities = capabilities
            result = {**health, "workspace_id": workspace, "connection_mode": "legacy",
                      "summary": {key: capabilities.get(key) for key in ("operations", "exports", "analysis")},
                      "help": {"api": "references/api.md", "recovery": "references/recovery.md"}}
        self._validate_identity(result)
        if result.get("connection_mode") != "legacy":
            if not isinstance(result.get("instance_id"), str) or not isinstance(result.get("workspace_id"), str):
                raise ClientError("IDENTITY_MISMATCH", "后台未提供实例和数据目录身份。")
            if self._workspace and result["workspace_id"] != self._workspace:
                raise ClientError("WORKSPACE_MISMATCH", "后台属于其他数据目录。")
            if self.instance.get("instance_id") and result["instance_id"] != self.instance["instance_id"]:
                raise ClientError("INSTANCE_CHANGED", "后台已重开，需要更新连接。", retryable=True)
            self._workspace = result["workspace_id"]
        return {**result, "base_url": self.base, "connection": "ready"}

    def connect(self, timeout: float = 10) -> ConnectionResult:
        if self._connection is not None:
            return self._connection
        deadline = time.monotonic() + timeout
        while True:
            try:
                self._connection = self._connect_once(deadline)
                return self._connection
            except HTTPError as exc:
                if exc.code in (401, 403) and self._rediscover():
                    continue
                raise self._http_error(exc) from exc
            except ClientError as exc:
                if exc.code in ("INSTANCE_CHANGED", "VERSION_MISMATCH") and self._rediscover():
                    continue
                raise
            except (URLError, TimeoutError, OSError) as exc:
                if self._rediscover():
                    continue
                raise ClientError("APP_UNREACHABLE", "软件后台暂时无法连接。", retryable=True,
                                  next_action="确认 Media Deep Researcher 已启动；若 Agent 在云端，需要当前电脑的执行能力。") from exc
            except (ValueError, KeyError, TypeError) as exc:
                raise ClientError("INVALID_RESPONSE", "后台返回了无法识别的连接信息。") from exc

    def send(self, method: str, path: str, data: bytes | None = None,
             headers: dict[str, str] | None = None, timeout: float = 60) -> bytes:
        self.connect()
        for attempt in range(2):
            try:
                return self._send(method, path, data, headers, timeout)
            except HTTPError as exc:
                if method.upper() in ("GET", "HEAD") and exc.code in (401, 403) and attempt == 0 and self._rediscover():
                    self.connect()
                    continue
                raise self._http_error(exc) from exc
            except (URLError, TimeoutError, OSError) as exc:
                if method.upper() in ("GET", "HEAD") and attempt == 0 and self._rediscover():
                    self.connect()
                    continue
                raise ClientError("APP_UNREACHABLE", "本机请求未能完成。", retryable=True) from exc

    def check(self) -> CheckResult:
        """Legacy command: keep full capability fields after an authorized connection."""
        health = self.connect()
        if self._capabilities is None:
            try:
                self._capabilities = json.loads(self._send("GET", "/api/v1/capabilities", timeout=5))
            except HTTPError as exc:
                raise self._http_error(exc) from exc
            self._validate_identity(self._capabilities)
        return {**health, "capabilities": self._capabilities}

    def request(
        self, method: str, path: str, data: bytes | None = None,
        content_type: str = "application/json", request_id: str | None = None, timeout: float = 60
    ) -> Any:
        method = method.upper()
        mutating = method not in ("GET", "HEAD")
        if not mutating:
            raw = self.send(method, path, data, {"Content-Type": content_type}, timeout)
            return json.loads(raw) if raw else {}
        request_id = request_id or uuid.uuid4().hex
        if not request_id or len(request_id) > 160 or any(
            c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_." for c in request_id
        ):
            raise ClientError("INVALID_REQUEST", "请求编号只能包含字母、数字、点、横线或下划线，长度至多160。")
        deadline = time.monotonic() + timeout
        try:
            self.connect(timeout=min(10, timeout))
        except ClientError as exc:
            exc.request_id = request_id
            raise
        headers = {"Content-Type": content_type}
        headers["Idempotency-Key"] = request_id
        folder = self.instance_path.parent / "client-receipts"
        # Windows filenames are case-insensitive and have reserved device names;
        # request identities in SQLite intentionally remain case-sensitive.
        journal = folder / ("req-" + hashlib.sha256(request_id.encode("utf-8")).hexdigest() + ".json")
        fingerprint = hashlib.sha256(method.encode() + path.encode() + (data or b"")).hexdigest()
        pending: JournalRecord = {"request_id": request_id, "method": method, "path": path,
                   "fingerprint": fingerprint, "workspace_id": self._workspace,
                   "content_type": content_type, "state": "pending"}
        with journal_lock(journal):
            previous = read_journal(journal, request_id) if journal.exists() else None
            if previous is None:
                reserved = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
                legacy = folder / (request_id + ".json")
                if request_id.split(".", 1)[0].upper() not in reserved and legacy.exists():
                    candidate = read_journal(legacy, request_id)
                    if candidate.get("request_id") == request_id:
                        previous = candidate
            if previous:
                if previous.get("fingerprint") != fingerprint:
                    raise ClientError("REQUEST_ID_CONFLICT", "请求编号已用于不同的请求内容。", request_id=request_id)
                if previous.get("content_type", content_type) != content_type:
                    raise ClientError("REQUEST_ID_CONFLICT", "请求编号已用于不同的请求类型。", request_id=request_id)
                if previous.get("workspace_id") and previous["workspace_id"] != self._workspace:
                    raise ClientError("WORKSPACE_MISMATCH", "此请求编号属于另一个数据目录。", request_id=request_id)
                if previous.get("state") == "completed" and previous.get("workspace_id") == self._workspace:
                    return previous["response"]
            atomic_journal(journal, pending)
        output({"request_id": request_id, "receipt": str(journal)}, error=True)

        def completed(result):
            with journal_lock(journal):
                current = read_journal(journal, request_id)
                if current.get("state") == "completed":
                    return current["response"]
                atomic_journal(journal, {**pending, "state": "completed", "response": result})
            return result

        needs_receipt = previous is not None
        for attempt in range(3):
            try:
                if needs_receipt:
                    try:
                        receipt = json.loads(self._send("GET", "/api/v1/requests/" + request_id,
                                                       timeout=self._remaining(deadline)))
                    except HTTPError as exc:
                        if exc.code != 404:
                            raise
                    else:
                        if receipt.get("scope") != method + " " + path.split("?", 1)[0]:
                            raise ClientError("REQUEST_ID_CONFLICT", "后台回执属于另一个操作。", request_id=request_id)
                        # Legacy receipts identify the scope, not the submitted body.
                        # Replay the original request so the durable transaction verifies
                        # ID + normalized body and returns its existing response safely.
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                raw = self._send(method, path, data, headers, timeout=remaining)
                result = json.loads(raw) if raw else {}
                return completed(result)
            except HTTPError as exc:
                if exc.code not in (401, 403) and exc.code < 500:
                    error = self._http_error(exc)
                    error.request_id = request_id
                    raise error from exc
                needs_receipt = True
                try:
                    if exc.code in (401, 403) and not self._rediscover():
                        error = self._http_error(exc)
                        error.request_id = request_id
                        raise error from exc
                    self.connect(timeout=min(10, max(0.001, deadline - time.monotonic())))
                except ClientError as error:
                    error.request_id = request_id
                    raise
            except (URLError, TimeoutError, OSError, ValueError) as exc:
                needs_receipt = True
                try:
                    if self._rediscover():
                        self.connect(timeout=min(10, max(0.001, deadline - time.monotonic())))
                except ClientError as error:
                    error.request_id = request_id
                    raise
            except ClientError as error:
                error.request_id = request_id
                raise
        raise ClientError("REQUEST_UNCONFIRMED", "请求结果尚未确认，已保留原请求编号。", retryable=True,
                          next_action="使用 receipt 查询原请求；恢复时使用相同编号和原请求内容，不另建任务。", request_id=request_id)


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--instance")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("connect", help="轻量确认本机身份、访问权限和能力摘要")
    commands.add_parser("check")
    explain = commands.add_parser('explain', help='读取原任务统一状态解释，不发起采集或探测')
    explain.add_argument('job_id')
    operation = commands.add_parser('operation', help='查询或取消指定操作')
    operation.add_argument('operation_id')
    operation.add_argument('--cancel',action='store_true')
    operation.add_argument('--request-id')
    probe = commands.add_parser('probe',help='按能力执行独立对照探测；目前仅抖音启用')
    probe.add_argument('platform',choices=('dy','xhs','ks','bili','wb','tieba','zhihu'))
    probe.add_argument('capability',choices=('search','detail','creator','comments','replies','profile'))
    probe.add_argument('--no-wait',action='store_true')
    probe.add_argument('--request-id')
    probe.add_argument('--restart-cycle',action='store_true',help='显式重开已耗尽的复查周期；登录和普通检查不重置次数')
    self_test = commands.add_parser("self-test", help="独立运行自检，不创建正式业务成果")
    self_test.add_argument("--local-only", action="store_true")
    actions = self_test.add_mutually_exclusive_group()
    actions.add_argument("--status", nargs="?", const="latest", metavar="ID")
    actions.add_argument("--cancel", metavar="ID")
    self_test.add_argument("--no-wait", action="store_true")
    self_test.add_argument("--request-id")
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
        health = client.connect()
        if args.command == "check":
            result = client.check()
        elif args.command == "connect":
            result = health
        elif args.command in ('explain','probe','operation'):
            if args.command not in health.get('summary',{}).get('operations',[]):
                raise ClientError('FEATURE_UNAVAILABLE','当前软件不支持状态与恢复命令。',next_action='升级至 0.5.0 或更新版本；旧任务和数据保留。')
            if args.command=='explain':
                result=json.loads(client.send('GET','/api/v1/jobs/'+args.job_id+'/explain'))
            elif args.command=='operation':
                path='/api/v1/operations/'+args.operation_id
                result=client.request('POST',path+'/cancel',b'{}',request_id=args.request_id) if args.cancel else json.loads(client.send('GET',path))
            else:
                result=client.request('POST','/api/v1/platforms/'+args.platform+'/probes',
                                      json.dumps({'capability':args.capability,**({'restart_cycle':True} if args.restart_cycle else {})}).encode(),request_id=args.request_id)
                deadline=time.monotonic()+65
                while not args.no_wait and result['state'] in ('accepted','running') and time.monotonic()<deadline:
                    time.sleep(.5)
                    result=json.loads(client.send('GET','/api/v1/operations/'+result['id']))
        elif args.command == "self-test":
            if "self_test" not in health.get("summary", {}).get("operations", []):
                raise ClientError("FEATURE_UNAVAILABLE", "当前软件不支持运行自检。", next_action="升级到支持独立运行自检的软件版本。")
            prefix = "/api/v1/diagnostics/self-tests"
            if args.status:
                result = json.loads(client.send("GET", prefix + "/" + args.status))
            elif args.cancel:
                result = client.request("POST", prefix + "/" + args.cancel + "/cancel", b"{}", request_id=args.request_id)
            else:
                result = client.request("POST", prefix, json.dumps({"include_network": not args.local_only}).encode(), request_id=args.request_id)
                deadline = time.monotonic() + 200
                previous = None
                while not args.no_wait and result["state"] in ("running", "cancelling"):
                    if time.monotonic() >= deadline:
                        output({"check_id": result["id"], "message": "等待结束，可用 self-test --status 查询同一次检查。"}, error=True)
                        break
                    status = [(step["id"], step["state"]) for step in result["steps"]]
                    if status != previous:
                        output({"check_id": result["id"], "steps": result["steps"]}, error=True)
                        previous = status
                    time.sleep(1)
                    result = json.loads(client.send("GET", prefix + "/" + result["id"]))
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
        if args.command == "self-test" and result and (result.get("local_state") == "failed" or result.get("state") in ("failed", "timed_out")):
            return 2
    except ClientError as exc:
        output(exc.as_dict())
        return 1
    except (OSError, ValueError) as exc:
        output(ClientError("INVALID_REQUEST", "无法读取请求文件或解析请求内容。", next_action="检查输入文件、UTF-8 编码及请求参数后重试。").as_dict())
        return 1
    except Exception as exc:
        output(ClientError("CLIENT_ERROR", "客户端未能完成操作（" + type(exc).__name__ + "）。", retryable=True).as_dict())
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
