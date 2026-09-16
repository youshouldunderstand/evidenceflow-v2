"""无凭据端到端演示：只用一个进程跑完整产品链路，零外部调用。

用法（在仓库根目录执行，一条命令）：

    Set-Location backend
    ..\\.venv\\Scripts\\python.exe -m app.demo_nocreds

它会：

1. 用显式 Fake Settings 在进程内启动真实 FastAPI 应用（不读取 `.env`、不读环境凭据）；
2. 通过 **真实 HTTP 路由** `POST /api/research` 创建任务，让真实工作流
   （规划 → 研究 → 写作 → 引文校验 → 审核 → 指标 → 落盘）跑完；
3. 打印任务终态、报告标题、质量指标、**Claim → Evidence → Source 引用链**；
4. 回放 SSE 事件流。

它证明的是**工程链路**（路由、工作流、预算、账本、持久化、SSE 都能跑），
**不是**互联网调研质量——Provider 是确定性的 Fake，报告内容不可作为事实结论。
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

import httpx

from app.config import Settings
from app.main import create_app

DEMO_QUERY = "SQLite WAL 模式下的并发读写与单写者限制"
TERMINAL = {"COMPLETED", "COMPLETED_WITH_WARNINGS", "FAILED", "BUDGET_EXCEEDED"}


def _demo_settings(database_path: Path) -> Settings:
    """显式构造 Fake 配置：不读 .env，也不需要任何 API Key。"""

    return Settings(
        app_name="EvidenceFlow",
        app_env="test",
        database_url=f"sqlite:///{database_path.as_posix()}",
        checkpoint_database_path=":memory:",
        llm_provider="fake",
        search_provider="fake",
        workflow_version="v2.0",
    )


def _sse_data_lines(body: str) -> list[str]:
    return [line for line in body.splitlines() if line.startswith("data:")]


async def main() -> int:
    with tempfile.TemporaryDirectory(prefix="evidenceflow-demo-") as tmp:
        settings = _demo_settings(Path(tmp) / "demo.db")
        application = create_app(settings)
        # httpx 的 ASGITransport 不触发 lifespan，这里显式进入，
        # 否则 research_service 不会被初始化。
        async with application.router.lifespan_context(application):
            transport = httpx.ASGITransport(app=application)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://demo", timeout=60.0
            ) as client:
                health = (await client.get("/health")).json()
                print("=" * 68)
                print("EvidenceFlow 无凭据演示（Fake Provider，零外部调用）")
                print("=" * 68)
                print(f"/health            : {health}")

                created = await client.post("/api/research", json={"query": DEMO_QUERY})
                created.raise_for_status()
                task_id = created.json()["task_id"]
                print(f"POST /api/research : {task_id}")

                task: dict[str, object] = {}
                for _ in range(120):
                    task = (await client.get(f"/api/research/{task_id}")).json()
                    if task.get("status") in TERMINAL:
                        break
                    await asyncio.sleep(0.25)
                print(f"终态 status        : {task.get('status')}")

                report = (await client.get(f"/api/reports/{task_id}")).json()
                metrics = report["metrics"]
                print(f"报告标题           : {report['report']['title']}")
                print(
                    "质量指标           : "
                    f"overall_score={metrics['overall_score']} "
                    f"passed={metrics['passed']} "
                    f"citation_coverage={metrics['citation_coverage']} "
                    f"citation_precision={metrics['citation_precision']}"
                )
                print(f"Provider 声明      : {report['provider_notice']}")
                print("-" * 68)
                print("Claim → Evidence → Source 引用链（URL 由 SourceRecord 渲染）")
                for citation in report["citations"]:
                    print(
                        f"  {citation['claim_id']} → {citation['evidence_id']} "
                        f"→ {citation['source_id']}"
                    )
                    print(f"    引文: {citation['quote'][:110]}")
                    print(f"    来源: {citation['source_title']} — {citation['source_url']}")
                print("-" * 68)
                events = await client.get(f"/api/research/{task_id}/events")
                lines = _sse_data_lines(events.text)
                print(f"SSE 事件帧         : {len(lines)} 帧（回放正常）")
                print("=" * 68)
                print("说明：以上为确定性 Fake 数据，只证明工程链路可运行，")
                print("      不代表互联网调研质量，也不构成架构优劣证据。")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
