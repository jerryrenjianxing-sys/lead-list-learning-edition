from typing import Any, Literal
from pydantic import BaseModel, Field, ConfigDict, model_validator

Platform = Literal["xhs", "dy", "ks", "bili", "wb", "tieba", "zhihu"]


class SelfTestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    include_network: bool = True


class CrawlRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    platform: Platform
    crawler_type: Literal["search", "detail", "creator", "login"] = "search"
    keywords: str = ""
    specified_ids: str = ""
    creator_ids: str = ""
    login_type: Literal["qrcode", "cookie", "phone"] = "qrcode"
    cookies: str = ""
    start_page: int = Field(1, ge=1)
    max_notes_count: int = Field(0, ge=0)
    max_comments_count: int = Field(0, ge=0)
    enable_comments: bool = True
    enable_sub_comments: bool = False
    enrich_profiles: bool = False
    headless: bool = False
    session_action: Literal["login", "check", "open"] = "login"

    @model_validator(mode="before")
    @classmethod
    def discard_retired_media_option(cls, values):
        # Older clients may still send this field; it is no longer a capability.
        if isinstance(values, dict):
            values = {k: v for k, v in values.items() if k != "enable_media"}
        return values

    @model_validator(mode="after")
    def inputs(self):
        if self.crawler_type == "login":
            self.keywords = self.specified_ids = self.creator_ids = ""
            self.enable_comments = self.enable_sub_comments = self.enrich_profiles = (
                False
            )
            self.headless = False
            self.start_page = 1
            self.max_notes_count = self.max_comments_count = 0
        field = {
            "search": "keywords",
            "detail": "specified_ids",
            "creator": "creator_ids",
        }.get(self.crawler_type)
        if field and not getattr(self, field).strip(" ,，\r\n\t"):
            raise ValueError(f"{field} is required for {self.crawler_type}")
        if self.enrich_profiles and self.platform not in ("xhs", "dy", "ks"):
            raise ValueError(
                "Public commenter-profile enrichment is currently available on xhs/dy/ks"
            )
        return self


class DatasetRequest(BaseModel):
    name: str = Field(min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict)
    records: list[dict[str, Any]] = Field(default_factory=list)


class JobHistoryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ids: list[str] | None = None


class AnalysisRequest(BaseModel):
    dataset_id: str
    name: str = Field(min_length=1)
    goal: str = Field(min_length=1)
    result_schema: dict[str, Any] | None = None
    record_ids: list[str] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class AnalysisResult(BaseModel):
    id: str | None = None
    payload: dict[str, Any]
    evidence_ids: list[str] = Field(default_factory=list)


class BatchRequest(BaseModel):
    batch_id: str = Field(min_length=1)
    results: list[AnalysisResult] = Field(default_factory=list)
    processed_ids: list[str] = Field(default_factory=list)
    skipped: dict[str, str] = Field(default_factory=dict)
    failed: dict[str, str] = Field(default_factory=dict)


class FinishRequest(BaseModel):
    allow_partial: bool = False


class ExportRequest(BaseModel):
    format: Literal["json", "csv", "xlsx", "docx", "md"] = "xlsx"
