# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler/blob/main/api/schemas/crawler.py
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

import os
import uuid
from enum import Enum
from pathlib import Path
from typing import Optional, Literal
from pydantic import BaseModel, Field, model_validator


MAX_API_LIMIT_COUNT = 10000


class PlatformEnum(str, Enum):
    """Supported media platforms"""
    XHS = "xhs"
    DOUYIN = "dy"
    KUAISHOU = "ks"
    BILIBILI = "bili"
    WEIBO = "wb"
    TIEBA = "tieba"
    ZHIHU = "zhihu"


class LoginTypeEnum(str, Enum):
    """Login method"""
    QRCODE = "qrcode"
    PHONE = "phone"
    COOKIE = "cookie"


class CrawlerTypeEnum(str, Enum):
    """Crawler type"""
    LOGIN = "login"
    SEARCH = "search"
    DETAIL = "detail"
    CREATOR = "creator"


class SaveDataOptionEnum(str, Enum):
    """Data save option"""
    CSV = "csv"
    DB = "db"
    JSON = "json"
    JSONL = "jsonl"
    SQLITE = "sqlite"
    MONGODB = "mongodb"
    EXCEL = "excel"


class CrawlerStartRequest(BaseModel):
    """Crawler start request"""
    task_id: str = Field(
        default_factory=lambda: uuid.uuid4().hex,
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
    )
    platform: PlatformEnum
    login_type: LoginTypeEnum = LoginTypeEnum.QRCODE
    crawler_type: CrawlerTypeEnum = CrawlerTypeEnum.SEARCH
    keywords: str = ""  # Keywords for search mode
    specified_ids: str = ""  # Post/video ID list for detail mode, comma-separated
    creator_ids: str = ""  # Creator ID list for creator mode, comma-separated
    start_page: int = 1
    enable_comments: bool = True
    enable_sub_comments: bool = False
    save_option: SaveDataOptionEnum = SaveDataOptionEnum.JSONL
    cookies: str = ""
    headless: bool = False
    max_notes_count: Optional[int] = Field(default=None, ge=0, le=MAX_API_LIMIT_COUNT)
    max_comments_count: Optional[int] = Field(default=None, ge=0, le=MAX_API_LIMIT_COUNT)
    save_data_path: str = ""
    lead_mode: bool = False
    lead_max_accounts: int = Field(default=0, ge=0, le=MAX_API_LIMIT_COUNT)
    lead_deep_profile_limit: int = Field(default=30, ge=0, le=MAX_API_LIMIT_COUNT)

    @model_validator(mode="after")
    def validate_lead_scope(self):
        required_input = {
            CrawlerTypeEnum.SEARCH: ("keywords", self.keywords),
            CrawlerTypeEnum.DETAIL: ("specified_ids", self.specified_ids),
            CrawlerTypeEnum.CREATOR: ("creator_ids", self.creator_ids),
        }.get(self.crawler_type)
        if required_input and not required_input[1].strip(" \t\r\n,，"):
            raise ValueError(
                f"{required_input[0]} is required for {self.crawler_type.value} mode"
            )
        if self.lead_mode and self.platform not in (
            PlatformEnum.XHS,
            PlatformEnum.DOUYIN,
            PlatformEnum.KUAISHOU,
        ):
            raise ValueError("lead mode currently supports only xhs, dy and ks")
        if self.save_data_path and not self.lead_mode:
            raise ValueError("save_data_path is available only in lead mode")
        if self.save_data_path:
            allowed_root = os.getenv("MEDIACRAWLER_ALLOWED_RUN_ROOT", "")
            if not allowed_root:
                raise ValueError("MEDIACRAWLER_ALLOWED_RUN_ROOT is required")
            output = Path(self.save_data_path).resolve()
            root = Path(allowed_root).resolve()
            if output != root and root not in output.parents:
                raise ValueError("save_data_path must be inside MEDIACRAWLER_ALLOWED_RUN_ROOT")
        return self


class CrawlerStopRequest(BaseModel):
    """Crawler stop request bound to the task that started it."""
    task_id: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
    )


class CrawlerStatusResponse(BaseModel):
    """Crawler status response"""
    status: Literal["idle", "running", "stopping", "error"]
    task_id: Optional[str] = None
    platform: Optional[str] = None
    crawler_type: Optional[str] = None
    started_at: Optional[str] = None
    error_message: Optional[str] = None
    recovery_type: Optional[
        Literal["xhs_verification", "xhs_resume", "profile_resume"]
    ] = None
    recovery_task_id: Optional[str] = None
    recovery_platform: Optional[Literal["dy", "ks", "xhs"]] = None
    recovery_reason: Optional[
        Literal["risk_control", "login_expired", "browser_closed", "network_error"]
    ] = None
    api_process_id: Optional[int] = None
    crawler_process_id: Optional[int] = None
    memory_metric: str = "unavailable"
    api_private_memory_bytes: int = 0
    crawler_tree_private_memory_bytes: int = 0
    total_private_memory_bytes: int = 0
    peak_total_private_memory_bytes: int = 0
    largest_process_name: Optional[str] = None
    largest_process_id: Optional[int] = None
    largest_process_private_memory_bytes: int = 0


class LogEntry(BaseModel):
    """Log entry"""
    id: int
    timestamp: str
    level: Literal["info", "warning", "error", "success", "debug"]
    message: str


class DataFileInfo(BaseModel):
    """Data file information"""
    name: str
    path: str
    size: int
    modified_at: str
    record_count: Optional[int] = None
