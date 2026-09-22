# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler/blob/main/api/services/crawler_manager.py
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#
# 声明：本代码仅供学习和研究目的使用。使用者应遵守以下原则：
# 1. 不得用于任何商业用途。
# 2. 使用时应遵守目标平台的使用条款和robots.txt规则。
# 3. 不得进行大规模爬取或对平台造成运营干扰。
# 4. 应合理控制请求频率，避免给目标平台带来不必要的负担。
# 5. 不得用于任何非法或不当的用途。
#
# 详细许可条款请参阅项目根目录下的LICENSE文件。
# 使用本代码即表示您同意遵守上述原则和LICENSE中的所有条款。

import asyncio
import json
import re
import subprocess
import os
import psutil
import sys
from typing import Optional, List
from datetime import datetime
from pathlib import Path

from ..schemas import CrawlerStartRequest, CrawlerTypeEnum, LogEntry


class CrawlerTaskConflict(Exception):
    """The caller does not own the active crawler task."""


class CrawlerTaskCancelled(Exception):
    """The crawler task was cancelled before it started."""


class CrawlerManager:
    """Crawler process manager"""

    def __init__(self):
        self._lock = asyncio.Lock()
        self.process: Optional[subprocess.Popen] = None
        self.status = "idle"
        self.started_at: Optional[datetime] = None
        self.current_config: Optional[CrawlerStartRequest] = None
        self.current_task_id: Optional[str] = None
        self._cancelled_task_ids: set[str] = set()
        self.error_message: Optional[str] = None
        self._log_id = 0
        self._logs: List[LogEntry] = []
        self._read_task: Optional[asyncio.Task] = None
        self._peak_total_private_memory_bytes = 0
        # Project root directory
        self._project_root = Path(__file__).parent.parent.parent
        # Log queue - for pushing to WebSocket
        self._log_queue: Optional[asyncio.Queue] = None

    @property
    def logs(self) -> List[LogEntry]:
        return self._logs

    def get_log_queue(self) -> asyncio.Queue:
        """Get or create log queue"""
        if self._log_queue is None:
            self._log_queue = asyncio.Queue(maxsize=500)
        return self._log_queue

    def _create_log_entry(self, message: str, level: str = "info") -> LogEntry:
        """Create log entry"""
        self._log_id += 1
        entry = LogEntry(
            id=self._log_id,
            timestamp=datetime.now().strftime("%H:%M:%S"),
            level=level,
            message=message
        )
        self._logs.append(entry)
        # Keep last 500 logs
        if len(self._logs) > 500:
            self._logs = self._logs[-500:]
        return entry

    async def _push_log(self, entry: LogEntry):
        """Push log to queue"""
        if self._log_queue is not None:
            try:
                if self._log_queue.full():
                    self._log_queue.get_nowait()
                self._log_queue.put_nowait(entry)
            except asyncio.QueueFull:
                pass

    def _parse_log_level(self, line: str) -> str:
        """Parse log level"""
        line_upper = line.upper()
        if re.search(r"\b(?:ERROR|FAILED)\b", line_upper):
            return "error"
        elif re.search(r"\b(?:WARNING|WARN)\b", line_upper):
            return "warning"
        elif "SUCCESS" in line_upper or "完成" in line or "成功" in line:
            return "success"
        elif "DEBUG" in line_upper:
            return "debug"
        return "info"

    @staticmethod
    def _sanitize_log_line(line: str) -> str:
        line = re.sub(
            r"(?i)(verifyuuid\s*[:=]\s*)[^\s,;&]+",
            r"\1[REDACTED]",
            line,
        )
        line = re.sub(
            r"""(?ix)
            (["']?(?:xsec(?:_(?:token|source))?|token|session|cookie|route_id|routing_id)["']?)
            \s*([:=])\s*
            (?:"[^"]*"|'[^']*'|[^&\s,;}]+)
            """,
            lambda match: f"{match.group(1)}{match.group(2)}[REDACTED]",
            line,
        )
        return re.sub(
            r"""(?ix)
            (["']?(?:raw_response|response_body|response|body)["']?)
            \s*([:=]).*$
            """,
            lambda match: f"{match.group(1)}{match.group(2)}[REDACTED]",
            line,
        )

    def _task_dir(self, task_id: str) -> Path:
        if self.current_config and self.current_config.task_id == task_id:
            if self.current_config.save_data_path:
                return Path(self.current_config.save_data_path)
        return self._project_root / "data" / "lead" / task_id

    def _write_task_manifest(self, config: CrawlerStartRequest, state: str) -> None:
        if not config.lead_mode or config.platform.value not in {"xhs", "dy", "ks"}:
            return
        task_dir = (
            Path(config.save_data_path)
            if config.save_data_path
            else self._project_root / "data" / "lead" / config.task_id
        )
        task_dir.mkdir(parents=True, exist_ok=True)
        manifest = task_dir / "task.json"
        temporary = manifest.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(
                {
                    "state": state,
                    "config": config.model_dump(mode="json", exclude={"cookies"}),
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        temporary.replace(manifest)

    def _recoverable_xhs_task(self, task_id: Optional[str] = None) -> Optional[dict]:
        lead_root = self._project_root / "data" / "lead"
        manifests = (
            [lead_root / task_id / "task.json"]
            if task_id
            else sorted(
                lead_root.glob("*/task.json"),
                key=lambda path: path.stat().st_mtime,
                reverse=True,
            )
            if lead_root.exists()
            else []
        )
        for manifest in manifests:
            try:
                data = json.loads(manifest.read_text(encoding="utf-8"))
                config_data = data["config"]
                if (
                    data.get("state") in {"running", "verification_required"}
                    and config_data.get("platform") == "xhs"
                ):
                    return {"path": manifest, "config": config_data}
            except (KeyError, OSError, ValueError):
                continue
        return None

    def _recoverable_profile_task(self, task_id: Optional[str] = None) -> Optional[dict]:
        lead_root = self._project_root / "data" / "lead"
        manifests = (
            [lead_root / task_id / "task.json"]
            if task_id
            else sorted(
                lead_root.glob("*/task.json"),
                key=lambda path: path.stat().st_mtime,
                reverse=True,
            )
            if lead_root.exists()
            else []
        )
        for manifest in manifests:
            try:
                data = json.loads(manifest.read_text(encoding="utf-8"))
                config_data = data["config"]
                platform = config_data.get("platform")
                if data.get("state") not in {"running", "error"}:
                    continue
                marker = manifest.parent / "profile_resume_required.json"
                if marker.exists():
                    marker_data = json.loads(marker.read_text(encoding="utf-8"))
                    reason = marker_data.get("reason")
                    if (
                        marker_data.get("platform") == platform
                        and platform in {"xhs", "dy", "ks"}
                        and reason
                        in {
                            "risk_control",
                            "login_expired",
                            "browser_closed",
                            "network_error",
                        }
                    ):
                        return {
                            "path": manifest,
                            "config": config_data,
                            "platform": platform,
                            "reason": reason,
                        }
                if platform == "dy" and (
                    manifest.parent / "douyin_profile_blocked"
                ).exists():
                    return {
                        "path": manifest,
                        "config": config_data,
                        "platform": "dy",
                        "reason": "risk_control",
                    }
            except (KeyError, OSError, ValueError):
                continue
        return None

    def _recovery_status(
        self,
    ) -> tuple[Optional[str], Optional[str], Optional[str], Optional[str]]:
        process_active = self.process is not None and self.process.poll() is None
        if process_active and self.current_task_id:
            marker = self._task_dir(self.current_task_id) / "xhs_verification_required"
            if marker.exists():
                if self.current_config:
                    self._write_task_manifest(
                        self.current_config, "verification_required"
                    )
                return "xhs_verification", self.current_task_id, "xhs", "risk_control"
            return None, None, None, None
        if self.current_task_id:
            recoverable = self._recoverable_profile_task(self.current_task_id)
            if recoverable:
                return (
                    "profile_resume",
                    self.current_task_id,
                    recoverable["platform"],
                    recoverable["reason"],
                )
            recoverable = self._recoverable_xhs_task(self.current_task_id)
            if recoverable:
                return "xhs_resume", self.current_task_id, "xhs", "risk_control"
        recoveries = [
            ("profile_resume", self._recoverable_profile_task()),
            ("xhs_resume", self._recoverable_xhs_task()),
        ]
        recoveries = [item for item in recoveries if item[1]]
        if recoveries:
            recovery_type, recoverable = max(
                recoveries,
                key=lambda item: item[1]["path"].stat().st_mtime,
            )
            if recovery_type == "profile_resume":
                return (
                    recovery_type,
                    recoverable["config"]["task_id"],
                    recoverable["platform"],
                    recoverable["reason"],
                )
            return recovery_type, recoverable["config"]["task_id"], "xhs", "risk_control"
        return None, None, None, None

    async def start(self, config: CrawlerStartRequest) -> bool:
        """Start crawler process"""
        async with self._lock:
            if config.task_id in self._cancelled_task_ids:
                self._cancelled_task_ids.discard(config.task_id)
                raise CrawlerTaskCancelled(
                    f"Crawler task {config.task_id} was cancelled"
                )
            if self.process and self.process.poll() is None:
                return False

            task_dir = (
                Path(config.save_data_path)
                if config.save_data_path
                else self._project_root / "data" / "lead" / config.task_id
            )
            for signal_name in (
                "xhs_verification_required",
                "xhs_resume",
                "douyin_profile_blocked",
                "profile_resume_required.json",
            ):
                (task_dir / signal_name).unlink(missing_ok=True)

            # Clear old logs
            self._logs = []
            self._peak_total_private_memory_bytes = 0
            self.error_message = None

            # Clear pending queue (don't replace object to avoid WebSocket broadcast coroutine holding old queue reference)
            if self._log_queue is None:
                self._log_queue = asyncio.Queue(maxsize=500)
            else:
                try:
                    while True:
                        self._log_queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass

            # Build command line arguments
            cmd = self._build_command(config)

            # Log start information
            entry = self._create_log_entry(self._command_summary(config), "info")
            await self._push_log(entry)

            try:
                # Start subprocess
                self.process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding='utf-8',
                    bufsize=1,
                    cwd=str(self._project_root),
                    env=self._build_env(config)
                )

                self.status = "running"
                self.started_at = datetime.now()
                self.current_config = config
                self.current_task_id = config.task_id
                self._write_task_manifest(config, "running")

                entry = self._create_log_entry(
                    f"Crawler started on platform: {config.platform.value}, type: {config.crawler_type.value}",
                    "success"
                )
                await self._push_log(entry)

                # Start log reading task
                self._read_task = asyncio.create_task(self._read_output())

                return True
            except Exception as e:
                self.status = "error"
                self.error_message = str(e)
                entry = self._create_log_entry(f"Failed to start crawler: {str(e)}", "error")
                await self._push_log(entry)
                return False

    async def stop(self, task_id: str) -> bool:
        """Stop crawler process"""
        async with self._lock:
            process_active = self.process is not None and self.process.poll() is None
            if not process_active:
                if task_id != self.current_task_id:
                    self._cancelled_task_ids.add(task_id)
                    return True
                return False
            if task_id != self.current_task_id:
                raise CrawlerTaskConflict(
                    f"Task {task_id} does not own active task {self.current_task_id}"
                )

            self.status = "stopping"
            entry = self._create_log_entry("Sending SIGTERM to crawler process...", "warning")
            await self._push_log(entry)

            try:
                survivors = await self._terminate_process_tree(self.process.pid)
                if survivors:
                    self.status = "error"
                    self.error_message = (
                        "Crawler processes survived forced termination: "
                        + ", ".join(map(str, survivors))
                    )
                    entry = self._create_log_entry(self.error_message, "error")
                    await self._push_log(entry)
                    return False

                entry = self._create_log_entry("Crawler process terminated", "info")
                await self._push_log(entry)

            except Exception as e:
                self.status = "error"
                self.error_message = str(e)
                entry = self._create_log_entry(f"Error stopping crawler: {str(e)}", "error")
                await self._push_log(entry)
                return False

            self.status = "idle"
            self.error_message = None
            if self.current_config:
                self._write_task_manifest(self.current_config, "cancelled")
            self.current_config = None

            # Cancel log reading task
            if self._read_task:
                self._read_task.cancel()
                self._read_task = None

            return True

    async def _terminate_process_tree(self, process_id: int) -> List[int]:
        """Terminate the crawler and every browser/worker process it created."""
        def terminate() -> List[int]:
            try:
                root = psutil.Process(process_id)
            except (psutil.Error, OSError):
                return []

            processes = []
            seen = set()

            def add_descendants(process) -> None:
                try:
                    children = process.children()
                except (psutil.Error, OSError):
                    children = []
                for child in children:
                    if child.pid in seen:
                        continue
                    seen.add(child.pid)
                    add_descendants(child)
                    processes.append(child)

            add_descendants(root)
            processes.append(root)

            for process in processes:
                try:
                    process.terminate()
                except (psutil.Error, OSError):
                    pass

            _, alive = psutil.wait_procs(processes, timeout=5)
            for process in alive:
                try:
                    process.kill()
                except (psutil.Error, OSError):
                    pass
            _, alive_after_kill = psutil.wait_procs(alive, timeout=5)
            survivors = []
            for process in alive_after_kill:
                try:
                    if process.is_running() and process.status() != psutil.STATUS_ZOMBIE:
                        survivors.append(process.pid)
                except (psutil.Error, OSError):
                    pass
            return survivors

        return await asyncio.get_running_loop().run_in_executor(None, terminate)

    def _memory_snapshot(self) -> dict:
        api_process_id = os.getpid()
        crawler_process_id = (
            self.process.pid
            if self.process and self.process.poll() is None
            else None
        )
        api_private_memory_bytes = 0
        crawler_tree_private_memory_bytes = 0
        sampled_metrics = set()
        largest_process_name = None
        largest_process_id = None
        largest_process_private_memory_bytes = 0

        def sample(process) -> int:
            nonlocal largest_process_name
            nonlocal largest_process_id
            nonlocal largest_process_private_memory_bytes
            try:
                memory_bytes = int(process.memory_full_info().uss)
                sampled_metrics.add("uss")
            except (AttributeError, psutil.Error, OSError):
                try:
                    memory_bytes = int(process.memory_info().private)
                    sampled_metrics.add("private_bytes")
                except (AttributeError, psutil.Error, OSError):
                    return 0
            try:
                if memory_bytes > largest_process_private_memory_bytes:
                    largest_process_name = process.name()
                    largest_process_id = process.pid
                    largest_process_private_memory_bytes = memory_bytes
                return memory_bytes
            except (psutil.Error, OSError):
                return 0

        try:
            api_private_memory_bytes = sample(psutil.Process(api_process_id))
        except (psutil.Error, OSError):
            pass

        if crawler_process_id is not None:
            try:
                root = psutil.Process(crawler_process_id)
                crawler_tree_private_memory_bytes = sum(
                    sample(process)
                    for process in [root, *root.children(recursive=True)]
                )
            except (psutil.Error, OSError):
                pass

        total_private_memory_bytes = (
            api_private_memory_bytes + crawler_tree_private_memory_bytes
        )
        self._peak_total_private_memory_bytes = max(
            self._peak_total_private_memory_bytes,
            total_private_memory_bytes,
        )
        memory_metric = (
            next(iter(sampled_metrics))
            if len(sampled_metrics) == 1
            else "uss_or_private_bytes"
            if sampled_metrics
            else "unavailable"
        )
        return {
            "api_process_id": api_process_id,
            "crawler_process_id": crawler_process_id,
            "memory_metric": memory_metric,
            "api_private_memory_bytes": api_private_memory_bytes,
            "crawler_tree_private_memory_bytes": crawler_tree_private_memory_bytes,
            "total_private_memory_bytes": total_private_memory_bytes,
            "peak_total_private_memory_bytes": self._peak_total_private_memory_bytes,
            "largest_process_name": largest_process_name,
            "largest_process_id": largest_process_id,
            "largest_process_private_memory_bytes": largest_process_private_memory_bytes,
        }

    def get_status(self, task_id: Optional[str] = None) -> dict:
        """Get current status"""
        if (
            task_id
            and self.current_task_id
            and task_id != self.current_task_id
        ):
            raise CrawlerTaskConflict(
                f"Task {task_id} does not match current task {self.current_task_id}"
            )
        (
            recovery_type,
            recovery_task_id,
            recovery_platform,
            recovery_reason,
        ) = self._recovery_status()
        return {
            "status": self.status,
            "task_id": self.current_task_id,
            "platform": self.current_config.platform.value if self.current_config else None,
            "crawler_type": self.current_config.crawler_type.value if self.current_config else None,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "error_message": self.error_message,
            "recovery_type": recovery_type,
            "recovery_task_id": recovery_task_id,
            "recovery_platform": recovery_platform,
            "recovery_reason": recovery_reason,
            **self._memory_snapshot(),
        }

    async def recover_xhs(self, task_id: str) -> bool:
        process_active = self.process is not None and self.process.poll() is None
        if process_active:
            if task_id != self.current_task_id:
                raise CrawlerTaskConflict(
                    f"Task {task_id} does not own active task {self.current_task_id}"
                )
            task_dir = self._task_dir(task_id)
            if not (task_dir / "xhs_verification_required").exists():
                return False
            (task_dir / "xhs_resume").touch()
            if self.current_config:
                self._write_task_manifest(self.current_config, "running")
            return True

        recoverable = self._recoverable_xhs_task(task_id)
        if not recoverable:
            return False
        config = CrawlerStartRequest.model_validate(recoverable["config"])
        config.crawler_type = CrawlerTypeEnum.LOGIN
        task_dir = recoverable["path"].parent
        for signal_name in ("xhs_verification_required", "xhs_resume"):
            (task_dir / signal_name).unlink(missing_ok=True)
        return await self.start(config)

    async def recover_profiles(self, task_id: str) -> bool:
        recoverable = self._recoverable_profile_task(task_id)
        if not recoverable:
            return False
        config = CrawlerStartRequest.model_validate(recoverable["config"])
        config.crawler_type = CrawlerTypeEnum.LOGIN
        task_dir = recoverable["path"].parent
        markers = {
            name: path.read_bytes()
            for name in ("profile_resume_required.json", "douyin_profile_blocked")
            if (path := task_dir / name).exists()
        }
        try:
            started = await self.start(config)
        except Exception:
            for name, data in markers.items():
                (task_dir / name).write_bytes(data)
            raise
        if not started:
            for name, data in markers.items():
                (task_dir / name).write_bytes(data)
        return started

    recover_douyin_profiles = recover_profiles

    def _build_command(self, config: CrawlerStartRequest) -> list:
        """Build main.py command line arguments"""
        cmd = [sys.executable, "main.py"]

        cmd.extend(["--platform", config.platform.value])
        cmd.extend(["--lt", config.login_type.value])
        cmd.extend(["--type", config.crawler_type.value])
        cmd.extend(["--save_data_option", config.save_option.value])

        # Pass different arguments based on crawler type
        if config.crawler_type.value == "search" and config.keywords:
            cmd.extend(["--keywords", config.keywords])
        elif config.crawler_type.value == "detail" and config.specified_ids:
            cmd.extend(["--specified_id", config.specified_ids])
        elif config.crawler_type.value == "creator" and config.creator_ids:
            cmd.extend(["--creator_id", config.creator_ids])

        if config.start_page != 1:
            cmd.extend(["--start", str(config.start_page)])

        cmd.extend(["--get_comment", "true" if config.enable_comments else "false"])
        cmd.extend(["--get_sub_comment", "true" if config.enable_sub_comments else "false"])
        cmd.extend(["--get_media", "false"])

        if config.max_notes_count is not None:
            cmd.extend(["--crawler_max_notes_count", str(config.max_notes_count)])

        if config.max_comments_count is not None:
            cmd.extend(["--max_comments_count_singlenotes", str(config.max_comments_count)])

        if config.cookies:
            cmd.extend(["--cookies", config.cookies])

        if config.save_data_path:
            cmd.extend(["--save_data_path", config.save_data_path])
        elif config.lead_mode:
            cmd.extend([
                "--save_data_path",
                str(self._project_root / "data" / "lead" / config.task_id),
            ])

        cmd.extend(["--headless", "true" if config.headless else "false"])

        return cmd

    def _build_env(self, config: CrawlerStartRequest) -> dict:
        return {
            **os.environ,
            "PYTHONUNBUFFERED": "1",
            "MEDIACRAWLER_LEAD_MODE": "1" if config.lead_mode else "0",
            "MEDIACRAWLER_LEAD_MAX_ACCOUNTS": str(config.lead_max_accounts),
            "MEDIACRAWLER_LEAD_DEEP_PROFILE_LIMIT": "30" if config.lead_mode else str(config.lead_deep_profile_limit),
            "MEDIACRAWLER_LEAD_MAX_COMMENTS": "300" if config.lead_mode else str(config.max_comments_count or 300),
        }

    @staticmethod
    def _command_summary(config: CrawlerStartRequest) -> str:
        output = config.save_data_path or (
            f"data/lead/{config.task_id}" if config.lead_mode else "default"
        )
        return (
            f"Starting crawler: platform={config.platform.value}, "
            f"type={config.crawler_type.value}, lead_mode={str(config.lead_mode).lower()}, "
            f"output={output}"
        )

    async def _read_output(self):
        """Asynchronously read process output"""
        loop = asyncio.get_event_loop()

        try:
            while self.process and self.process.poll() is None:
                # Read a line in thread pool
                line = await loop.run_in_executor(
                    None, self.process.stdout.readline
                )
                if line:
                    line = self._sanitize_log_line(line.strip())
                    if line:
                        level = self._parse_log_level(line)
                        entry = self._create_log_entry(line, level)
                        await self._push_log(entry)

            # Read remaining output
            if self.process and self.process.stdout:
                remaining = await loop.run_in_executor(
                    None, self.process.stdout.read
                )
                if remaining:
                    for line in remaining.strip().split('\n'):
                        if line.strip():
                            line = self._sanitize_log_line(line.strip())
                            level = self._parse_log_level(line)
                            entry = self._create_log_entry(line, level)
                            await self._push_log(entry)

            # Process ended
            if self.status == "running":
                exit_code = self.process.returncode if self.process else -1
                if exit_code == 0:
                    entry = self._create_log_entry("Crawler completed successfully", "success")
                    self.status = "idle"
                    self.error_message = None
                    if self.current_config:
                        self._write_task_manifest(self.current_config, "completed")
                else:
                    self.error_message = f"Crawler exited with code: {exit_code}"
                    entry = self._create_log_entry(self.error_message, "error")
                    self.status = "error"
                    if self.current_config:
                        self._write_task_manifest(self.current_config, "error")
                await self._push_log(entry)

        except asyncio.CancelledError:
            pass
        except Exception as e:
            message = f"Error reading crawler output: {str(e)}"
            if self.status == "running":
                self.status = "error"
                self.error_message = message
                if self.current_config:
                    self._write_task_manifest(self.current_config, "error")
            entry = self._create_log_entry(message, "error")
            await self._push_log(entry)


# Global singleton
crawler_manager = CrawlerManager()
