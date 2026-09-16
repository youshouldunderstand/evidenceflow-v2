"""FastAPI 应用入口。"""

from __future__ import annotations

from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

import aiosqlite
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from app.agents.manager import ManagerAgent
from app.agents.researcher import ResearcherAgent
from app.agents.reviewer import ReviewerAgent
from app.api.reports import router as reports_router
from app.api.research import router as research_router
from app.api.sources import router as sources_router
from app.config import Settings, load_env_file
from app.persistence.database import (
    init_database,
    make_engine,
    make_session_factory,
)
from app.persistence.repositories import ResearchRepository
from app.schemas.research import HealthResponse
from app.services.llm import DemoStructuredLLM, OpenAICompatibleStructuredLLM
from app.services.budget_factory import build_workflow_budget
from app.services.research_service import ResearchService
from app.services.search_factory import build_search_stack
from app.workflow.graph import EvidenceFlowWorkflow


def create_app(settings: Settings | None = None) -> FastAPI:
    """使用显式且可替换的依赖创建应用实例。

    未显式传入 `settings` 时，先加载 `backend/.env` 再读环境变量——
    否则配置了真实凭据的 `.env` 会被完全忽略，服务静默退回 Fake Provider，
    使"按文档启动"与"真实调用"不一致。已存在的环境变量优先于 `.env`；
    显式传入 `settings`（测试与 Fake 演示走这条路）时完全不读 `.env`。
    """

    if settings is None:
        load_env_file()
        resolved_settings = Settings.from_env()
    else:
        resolved_settings = settings
    engine = make_engine(resolved_settings.database_url)

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        init_database(engine)
        application.state.database_engine = engine
        repository = ResearchRepository(make_session_factory(engine))
        if resolved_settings.llm_provider == "fake":
            llm = DemoStructuredLLM()
            llm_notice = "FAKE MODEL: deterministic structured demo model."
        elif resolved_settings.openai_api_key:
            llm = OpenAICompatibleStructuredLLM(
                api_key=resolved_settings.openai_api_key,
                base_url=resolved_settings.openai_base_url,
                model=resolved_settings.openai_model,
                max_output_tokens=resolved_settings.openai_max_tokens,
                timeout_seconds=resolved_settings.openai_timeout_seconds,
            )
            llm_notice = "GLM-5.2 model through an OpenAI-compatible API."
        else:
            application.state.research_service = None
            application.state.research_service_error = (
                "LLM_PROVIDER=glm requires OPENAI_API_KEY"
            )
            yield
            engine.dispose()
            return

        try:
            search_stack = build_search_stack(resolved_settings)
        except ValueError as exc:
            application.state.research_service = None
            application.state.research_service_error = str(exc)
            yield
            engine.dispose()
            return

        if (
            resolved_settings.llm_provider == "fake"
            and resolved_settings.search_provider == "fake"
        ):
            notice = (
                "FAKE PROVIDERS: deterministic model, search results, and web page; "
                "this is not internet research."
            )
        else:
            notice = f"{llm_notice} {search_stack.notice}"

        checkpoint_path = (
            ":memory:"
            if resolved_settings.app_env == "test"
            else resolved_settings.checkpoint_database_path
        )
        async with aiosqlite.connect(checkpoint_path) as checkpoint_connection:
            checkpointer = AsyncSqliteSaver(
                checkpoint_connection,
                serde=JsonPlusSerializer(allowed_msgpack_modules=None),
            )
            manager = ManagerAgent(llm)
            researcher = ResearcherAgent(
                llm,
                search_stack.provider,
                search_stack.web_reader,
            )
            reviewer = ReviewerAgent(llm)
            workflow = EvidenceFlowWorkflow(
                manager,
                researcher,
                reviewer,
                repository,
                workflow_version=resolved_settings.workflow_version,
                budget=build_workflow_budget(resolved_settings),
                model_output_reserve_tokens=(
                    resolved_settings.workflow_model_output_reserve_tokens
                ),
                checkpointer=checkpointer,
            )
            application.state.research_service = ResearchService(
                repository,
                workflow,
                workflow_version=resolved_settings.workflow_version,
                provider_notice=notice,
            )
            yield
        engine.dispose()

    application = FastAPI(
        title=resolved_settings.app_name,
        version=resolved_settings.workflow_version,
        description="EvidenceFlow V2 evidence-backed research API",
        lifespan=lifespan,
    )
    application.state.settings = resolved_settings
    application.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type", "Last-Event-ID"],
    )
    application.include_router(research_router)
    application.include_router(reports_router)
    application.include_router(sources_router)

    @application.get("/health", response_model=HealthResponse, tags=["system"])
    async def health() -> HealthResponse:
        service = getattr(application.state, "research_service", None)
        return HealthResponse(
            status="ok",
            app=resolved_settings.app_name,
            workflow_version=resolved_settings.workflow_version,
            provider=f"{resolved_settings.llm_provider}+{resolved_settings.search_provider}",
            provider_notice=(
                getattr(service, "provider_notice", None) if service else None
            ),
            research_service_error=getattr(
                application.state, "research_service_error", None
            ),
        )

    return application


app = create_app()
