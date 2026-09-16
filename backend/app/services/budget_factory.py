"""把经过校验的环境配置转换成单次工作流预算。"""

from app.config import Settings
from app.workflow.budget import WorkflowBudget


def build_workflow_budget(settings: Settings) -> WorkflowBudget:
    """统一构建工作流预算，避免 API 与评测环境使用不同限制。"""

    return WorkflowBudget(
        max_model_calls=settings.workflow_max_model_calls,
        max_tool_calls=settings.workflow_max_tool_calls,
        max_search_calls=settings.workflow_max_search_calls,
        max_pages=settings.workflow_max_pages,
        max_tokens=settings.workflow_max_tokens,
        research_max_model_calls=settings.workflow_research_max_model_calls,
        downstream_reserved_model_calls=(
            settings.workflow_downstream_reserved_model_calls
        ),
        downstream_reserved_tokens=settings.workflow_downstream_reserved_tokens,
        max_no_progress_steps=settings.workflow_max_no_progress_steps,
        max_supplement_rounds=settings.workflow_max_supplement_rounds,
        supplement_max_model_calls=settings.workflow_supplement_max_model_calls,
        post_supplement_reserved_model_calls=(
            settings.workflow_post_supplement_reserved_model_calls
        ),
        post_supplement_reserved_tokens=(
            settings.workflow_post_supplement_reserved_tokens
        ),
    )
