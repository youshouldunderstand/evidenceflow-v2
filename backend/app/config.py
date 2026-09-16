"""从环境变量加载应用配置。"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


def project_env_path() -> Path:
    """`backend/.env` 的路径，按文件位置解析而不是按当前工作目录。

    这样无论从仓库根目录还是 `backend/` 启动，都能找到同一份配置。
    """

    return Path(__file__).resolve().parents[1] / ".env"


def load_env_file(path: Path | None = None, *, override: bool = False) -> Path | None:
    """把 `.env` 读进进程环境变量，返回实际加载的文件路径。

    约定（与 12-factor 一致，也是本项目此前只散落在评测入口里的行为）：

    - 文件不存在时**静默返回 None**，使无凭据环境仍能启动 Fake 流程；
    - 默认**不覆盖**已存在的环境变量，因此 shell 中显式导出的值优先于 `.env`；
    - 只做 `KEY=VALUE` 解析，支持外层单/双引号剥离，不执行任何 shell 展开。
    """

    env_path = path or project_env_path()
    if not env_path.exists():
        return None
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            continue
        value = value.strip().strip("\"'")
        if override:
            os.environ[key] = value
        else:
            os.environ.setdefault(key, value)
    return env_path


class Settings(BaseModel):
    """经过校验的运行时配置。

    密钥保持可选，使 API 在不调用模型时仍可启动；真正执行 GLM 任务前会明确拒绝空密钥。
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    app_name: str = "EvidenceFlow"
    app_env: Literal["development", "test", "production"] = "development"
    database_url: str = "sqlite:///./evidence_flow.db"
    checkpoint_database_path: str = "./evidence_flow_checkpoints.db"

    openai_api_key: str | None = Field(default=None, repr=False)
    openai_base_url: str = "https://open.bigmodel.cn/api/paas/v4"
    openai_model: str = "glm-5.2"
    openai_max_tokens: int = Field(default=4096, gt=0)
    openai_timeout_seconds: float = Field(default=180, gt=0, le=600)
    llm_provider: Literal["fake", "glm"] = "fake"

    search_provider: Literal["fake", "google", "tavily"] = "fake"
    tavily_api_key: str | None = Field(default=None, repr=False)
    tavily_search_max_results: int = Field(default=5, ge=1, le=20)
    tavily_search_depth: Literal[
        "basic", "fast", "ultra-fast", "advanced"
    ] = "basic"
    google_search_api_key: str | None = Field(default=None, repr=False)
    google_search_engine_id: str | None = Field(default=None, repr=False)
    google_search_num_results: int = Field(default=5, ge=1, le=10)
    google_search_language: str | None = None

    workflow_max_model_calls: int = Field(default=8, gt=0)
    workflow_max_tool_calls: int = Field(default=30, gt=0)
    workflow_max_search_calls: int = Field(default=8, gt=0)
    workflow_max_pages: int = Field(default=15, gt=0)
    workflow_max_tokens: int = Field(default=50_000, gt=0)
    workflow_model_output_reserve_tokens: int = Field(default=4096, gt=0)
    workflow_research_max_model_calls: int = Field(default=4, gt=0)
    workflow_downstream_reserved_model_calls: int = Field(default=2, ge=0)
    workflow_downstream_reserved_tokens: int = Field(default=12_000, ge=0)
    workflow_max_no_progress_steps: int = Field(default=2, gt=0)
    workflow_max_supplement_rounds: int = Field(default=1, ge=0)
    workflow_supplement_max_model_calls: int = Field(default=2, gt=0)
    workflow_post_supplement_reserved_model_calls: int = Field(default=2, ge=0)
    workflow_post_supplement_reserved_tokens: int = Field(default=8_000, ge=0)

    workflow_version: str = "v2.0"

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "Settings":
        """仅从项目支持的环境变量构建配置。"""

        values = os.environ if environ is None else environ
        api_key = values.get("OPENAI_API_KEY") or None
        return cls(
            app_name=values.get("APP_NAME", "EvidenceFlow"),
            app_env=values.get("APP_ENV", "development"),
            database_url=values.get(
                "DATABASE_URL", "sqlite:///./evidence_flow.db"
            ),
            checkpoint_database_path=values.get(
                "CHECKPOINT_DATABASE_PATH", "./evidence_flow_checkpoints.db"
            ),
            openai_api_key=api_key,
            openai_base_url=values.get(
                "OPENAI_BASE_URL", "https://open.bigmodel.cn/api/paas/v4"
            ),
            openai_model=values.get("OPENAI_MODEL", "glm-5.2"),
            openai_max_tokens=values.get("OPENAI_MAX_TOKENS", "4096"),
            openai_timeout_seconds=values.get("OPENAI_TIMEOUT_SECONDS", "180"),
            llm_provider=values.get("LLM_PROVIDER", "fake"),
            search_provider=values.get("SEARCH_PROVIDER", "fake"),
            tavily_api_key=values.get("TAVILY_API_KEY") or None,
            tavily_search_max_results=values.get(
                "TAVILY_SEARCH_MAX_RESULTS", "5"
            ),
            tavily_search_depth=values.get(
                "TAVILY_SEARCH_DEPTH", "basic"
            ),
            google_search_api_key=(
                values.get("GOOGLE_SEARCH_API_KEY") or None
            ),
            google_search_engine_id=(
                values.get("GOOGLE_SEARCH_ENGINE_ID") or None
            ),
            google_search_num_results=values.get(
                "GOOGLE_SEARCH_NUM_RESULTS", "5"
            ),
            google_search_language=(
                values.get("GOOGLE_SEARCH_LANGUAGE") or None
            ),
            workflow_max_model_calls=values.get(
                "WORKFLOW_MAX_MODEL_CALLS", "8"
            ),
            workflow_max_tool_calls=values.get(
                "WORKFLOW_MAX_TOOL_CALLS", "30"
            ),
            workflow_max_search_calls=values.get(
                "WORKFLOW_MAX_SEARCH_CALLS", "8"
            ),
            workflow_max_pages=values.get("WORKFLOW_MAX_PAGES", "15"),
            workflow_max_tokens=values.get("WORKFLOW_MAX_TOKENS", "50000"),
            workflow_model_output_reserve_tokens=values.get(
                "WORKFLOW_MODEL_OUTPUT_RESERVE_TOKENS", "4096"
            ),
            workflow_research_max_model_calls=values.get(
                "WORKFLOW_RESEARCH_MAX_MODEL_CALLS", "4"
            ),
            workflow_downstream_reserved_model_calls=values.get(
                "WORKFLOW_DOWNSTREAM_RESERVED_MODEL_CALLS", "2"
            ),
            workflow_downstream_reserved_tokens=values.get(
                "WORKFLOW_DOWNSTREAM_RESERVED_TOKENS", "12000"
            ),
            workflow_max_no_progress_steps=values.get(
                "WORKFLOW_MAX_NO_PROGRESS_STEPS", "2"
            ),
            workflow_max_supplement_rounds=values.get(
                "WORKFLOW_MAX_SUPPLEMENT_ROUNDS", "1"
            ),
            workflow_supplement_max_model_calls=values.get(
                "WORKFLOW_SUPPLEMENT_MAX_MODEL_CALLS", "2"
            ),
            workflow_post_supplement_reserved_model_calls=values.get(
                "WORKFLOW_POST_SUPPLEMENT_RESERVED_MODEL_CALLS", "2"
            ),
            workflow_post_supplement_reserved_tokens=values.get(
                "WORKFLOW_POST_SUPPLEMENT_RESERVED_TOKENS", "8000"
            ),
            workflow_version=values.get("WORKFLOW_VERSION", "v2.0"),
        )

    @model_validator(mode="after")
    def validate_stage_budget(self) -> "Settings":
        if (
            self.workflow_downstream_reserved_model_calls
            >= self.workflow_max_model_calls
        ):
            raise ValueError(
                "WORKFLOW_MAX_MODEL_CALLS must exceed downstream model reserve"
            )
        if self.workflow_downstream_reserved_tokens >= self.workflow_max_tokens:
            raise ValueError(
                "WORKFLOW_MAX_TOKENS must exceed downstream token reserve"
            )
        if (
            self.workflow_post_supplement_reserved_model_calls
            >= self.workflow_max_model_calls
        ):
            raise ValueError(
                "WORKFLOW_MAX_MODEL_CALLS must exceed post-supplement reserve"
            )
        if (
            self.workflow_post_supplement_reserved_tokens
            >= self.workflow_max_tokens
        ):
            raise ValueError(
                "WORKFLOW_MAX_TOKENS must exceed post-supplement reserve"
            )
        return self
