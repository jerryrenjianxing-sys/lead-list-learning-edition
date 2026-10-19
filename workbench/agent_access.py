"""One credential-free Interface for local Agent discovery and guidance."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import uuid
from typing import Any, Literal, TypedDict

from . import __version__
from .paths import DISPLAY_NAME, PRODUCT
from .queue import PLATFORMS


class AgentIdentity(TypedDict):
    product: str
    display_name: str
    version: str
    api_version: int
    status: Literal["ok"]


class CapabilitySummary(TypedDict):
    operations: list[str]
    platforms: list[str]
    exports: list[str]
    analysis: Literal["user_defined"]
    live_validation: Literal["pending"]


class AgentHandshake(AgentIdentity):
    instance_id: str
    workspace_id: str
    capability_revision: str
    summary: CapabilitySummary
    help: dict[str, str]


class AgentAccess:
    def __init__(self, root: str | Path, skill_dir: str | Path,
                 python_path: str | Path, instance_path: str | Path) -> None:
        self.root = Path(root).resolve()
        self.skill_dir = Path(skill_dir).absolute()
        self.python_path = Path(python_path).absolute()
        self.instance_path = Path(instance_path).absolute()
        self.instance_id = uuid.uuid4().hex
        canonical = os.path.normcase(os.path.realpath(self.root))
        self.workspace_id = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        self.help = {
            "api": "references/api.md", "recovery": "references/recovery.md",
            "self_test": "references/self-test.md", "lead_analysis": "references/lead-analysis.md",
        }
        self._capabilities: dict[str, Any] = {
            **self._identity(),
            "platforms": [
                {"id": p, "name": name, "modes": ["search", "detail", "creator", "login"],
                 "comments": True, "enrich_profiles": p in ("xhs", "dy", "ks"),
                 "media": False, "live_validation": "pending"}
                for p, name in PLATFORMS.items()
            ],
            "operations": ["jobs", "datasets", "analyses", "artifacts", "settings", "diagnostics", "self_test", "imports"],
            "analysis": "user_defined", "exports": ["csv", "json", "xlsx", "docx", "md"],
            "openapi": "/openapi.json",
            "agent_compatibility": {"codex": "fresh_agent_validation_pending", "hermes": "fresh_agent_validation_pending"},
            "resume_strategy": "Saved records are deduplicated on replay; analysis snapshots and coverage persist.",
        }
        revision = {
            key: value for key, value in self._capabilities.items()
            if key not in self._identity()
        }
        revision["help"] = self.help
        self.capability_revision = hashlib.sha256(
            json.dumps(revision, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()

    @staticmethod
    def _identity() -> AgentIdentity:
        return {"product": PRODUCT, "display_name": DISPLAY_NAME, "version": __version__, "api_version": 1, "status": "ok"}

    def capabilities(self) -> dict[str, Any]:
        """Keep the v1 capability contract available to old clients."""
        return deepcopy(self._capabilities)

    def handshake(self) -> AgentHandshake:
        """Route callers must enforce local authentication before invoking this."""
        return {
            **self._identity(), "instance_id": self.instance_id, "workspace_id": self.workspace_id,
            "capability_revision": self.capability_revision,
            "summary": {
                "operations": list(self._capabilities["operations"]),
                "platforms": list(PLATFORMS), "exports": list(self._capabilities["exports"]),
                "analysis": "user_defined",
                "live_validation": "pending",
            },
            "help": dict(self.help),
        }

    def _connection_parts(self, base_url: str) -> tuple[str, str, str]:
        def quoted(value):
            return "'" + str(value).replace("'", "''") + "'"

        skill = self.skill_dir / "SKILL.md"
        script = self.skill_dir / "scripts" / "workbench.py"
        command = f"& {quoted(self.python_path)} {quoted(script)} --instance {quoted(self.instance_path)} connect"
        guide = f"请使用本机 {DISPLAY_NAME} 协助我。读取完整 Skill：{skill}。"
        behavior = (
            "客户端会确认本机身份和访问权限；不要读取或输出实例文件、访问凭据或 Cookie。"
            "连接后按我的要求执行；没有具体任务时简短说明能力。连接、能力介绍和正常研究不运行自检；"
            "只有我要求验证安装、整套运行或排查基础故障时，才按需运行独立自检。"
            "分析主题、方法、字段及输出由我决定，正常任务进度和成果保存回软件。\n"
            f"只有本机文件无法访问时再获取备用完整 Skill ZIP：{base_url.rstrip('/')}/api/v1/skill。"
            "若 Agent 在云端或其他电脑，说明需要本机执行能力，不重复尝试 localhost。"
        )
        return guide, command, behavior

    def connect_text(self, base_url: str) -> str:
        guide, command, behavior = self._connection_parts(base_url)
        return f"{guide}\nWindows 客户端（使用软件内置 Python）：\n{command}\n{behavior}"

    def connect_markdown(self, base_url: str) -> str:
        guide, command, behavior = self._connection_parts(base_url)
        return (
            f"# {DISPLAY_NAME} Skill\n\n{guide}\n\n"
            f"使用软件内置 Python 完成本机连接：\n\n```powershell\n{command}\n```\n\n{behavior}\n\n"
            "这是聊天附件形式的本机接入入口，文件可以改名；完整 Skill 和客户端由已安装的软件提供。\n"
        )

