"""封装 LangGraph 执行、终态发布和工作流生命周期事件。"""

from __future__ import annotations

import asyncio
import time
from uuid import uuid4

from app.persistence.repositories import ResearchRepository
from app.reliability.exceptions import (
    AgentError,
    BudgetExceeded,
    ResumeConflictError,
)
from app.schemas.common import TaskStatus
from app.schemas.event import WorkflowEventType
from app.workflow.graph import EvidenceFlowWorkflow
from app.workflow.state import ResearchState


class RunLeaseLost(Exception):
    """Stop the old executor without overwriting the replacement owner's status."""


class ResearchService:
    def __init__(
        self,
        repository: ResearchRepository,
        workflow: EvidenceFlowWorkflow,
        *,
        workflow_version: str,
        provider_notice: str,
        lease_seconds: float = 3600,
        lease_heartbeat_seconds: float = 60,
    ) -> None:
        if not 0 < lease_heartbeat_seconds < lease_seconds:
            raise ValueError("lease heartbeat must be positive and shorter than lease")
        self._lease_seconds = lease_seconds
        self._lease_heartbeat_seconds = lease_heartbeat_seconds
        self.repository = repository
        self._workflow = workflow
        self._workflow_version = workflow_version
        self.provider_notice = provider_notice

    def create_task(self, query: str, depth: str) -> str:
        task_id = f"TASK_{uuid4().hex[:12]}"
        self.repository.create_task(
            task_id=task_id,
            query=query,
            depth=depth,
            workflow_version=self._workflow_version,
        )
        return task_id

    def reserve_resume(self, task_id: str) -> str:
        task = self.repository.get_task(task_id)
        if task is None:
            raise LookupError(f"Research task not found: {task_id}")
        if task.status in {
            TaskStatus.COMPLETED,
            TaskStatus.COMPLETED_WITH_WARNINGS,
        }:
            raise ResumeConflictError("Completed research cannot be resumed")
        owner = self.repository.acquire_run_lease(task_id, lease_seconds=self._lease_seconds)
        if owner is None:
            raise ResumeConflictError("Research task is already running")
        return owner

    async def run_task(
        self,
        task_id: str,
        query: str,
        *,
        lease_owner: str | None = None,
    ) -> ResearchState | None:
        owner = lease_owner or self.repository.acquire_run_lease(task_id, lease_seconds=self._lease_seconds)
        if owner is None or not self.repository.owns_run_lease(task_id, owner):
            return None
        started_at = time.perf_counter()
        self.repository.append_event(
            task_id,
            WorkflowEventType.WORKFLOW_STARTED,
            stage=TaskStatus.PENDING.value,
            payload={"resume_supported": True},
        )
        try:
            state = await self._run_while_leased(task_id, query, owner)
            self.repository.append_event(
                task_id,
                WorkflowEventType.WORKFLOW_COMPLETED,
                stage=state["current_stage"],
                payload={"revision_count": state["revision_count"]},
            )
            self._save_execution_stats(task_id, started_at)
            self.repository.update_status(
                task_id, TaskStatus(state["current_stage"])
            )
            return state
        except RunLeaseLost:
            # Another owner may already be running; do not publish a false terminal.
            return None
        except BudgetExceeded as exc:
            self._save_execution_stats(task_id, started_at)
            self.repository.append_event(
                task_id,
                WorkflowEventType.WORKFLOW_BUDGET_EXCEEDED,
                stage=TaskStatus.BUDGET_EXCEEDED.value,
                payload={
                    "error_type": type(exc).__name__,
                    "public_message": "任务在真实调用前因预算不足停止。",
                },
            )
            self.repository.update_status(
                task_id, TaskStatus.BUDGET_EXCEEDED, str(exc)
            )
            return None
        except AgentError as exc:
            self._save_execution_stats(task_id, started_at)
            self.repository.append_event(
                task_id,
                WorkflowEventType.WORKFLOW_FAILED,
                stage=TaskStatus.FAILED.value,
                payload={
                    "error_type": type(exc).__name__,
                    "public_message": "工作流执行失败，请根据错误分类检查或重试。",
                },
            )
            self.repository.update_status(task_id, TaskStatus.FAILED, str(exc))
            return None
        except Exception as exc:
            self._save_execution_stats(task_id, started_at)
            message = f"未预期的工作流错误：{type(exc).__name__}: {exc}"
            self.repository.append_event(
                task_id,
                WorkflowEventType.WORKFLOW_FAILED,
                stage=TaskStatus.FAILED.value,
                payload={
                    "error_type": type(exc).__name__,
                    "public_message": "工作流发生未预期错误，内部细节未公开。",
                },
            )
            self.repository.update_status(task_id, TaskStatus.FAILED, message)
            return None
        finally:
            self.repository.release_run_lease(task_id, owner)

    async def _run_while_leased(
        self, task_id: str, query: str, owner: str
    ) -> ResearchState:
        async def heartbeat() -> None:
            while True:
                await asyncio.sleep(self._lease_heartbeat_seconds)
                try:
                    renewed = self.repository.renew_run_lease(
                        task_id, owner, lease_seconds=self._lease_seconds
                    )
                except Exception as exc:
                    raise RunLeaseLost("Could not confirm run ownership") from exc
                if not renewed:
                    raise RunLeaseLost("Run lease expired or was replaced")

        work = asyncio.create_task(self._workflow.run(task_id, query))
        renewal = asyncio.create_task(heartbeat())
        try:
            done, _ = await asyncio.wait(
                {work, renewal}, return_when=asyncio.FIRST_COMPLETED
            )
            if renewal in done:
                renewal.result()
            if not self.repository.owns_run_lease(task_id, owner):
                raise RunLeaseLost("Run ownership lost before final status")
            return work.result()
        finally:
            for pending in (work, renewal):
                if not pending.done():
                    pending.cancel()
            await asyncio.gather(work, renewal, return_exceptions=True)

    def _save_execution_stats(self, task_id: str, started_at: float) -> None:
        execution = self.repository.summarize_call_attempts(
            task_id,
            latency_ms=(time.perf_counter() - started_at) * 1000,
        )
        self.repository.save_execution_stats(task_id, execution)
