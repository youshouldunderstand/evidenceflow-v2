"""SQLAlchemy engine, base, session, and schema initialization."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Engine, MetaData, create_engine, inspect, select, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from sqlalchemy.pool import StaticPool


NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}
CURRENT_SCHEMA_VERSION = 8


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def make_engine(database_url: str) -> Engine:
    """Create an engine that works for file and in-memory SQLite databases."""

    kwargs: dict[str, object] = {"pool_pre_ping": True}
    if database_url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
        if database_url in {"sqlite://", "sqlite:///:memory:"}:
            kwargs["poolclass"] = StaticPool
    return create_engine(database_url, **kwargs)


def make_session_factory(engine: Engine) -> sessionmaker:
    """Create a SQLAlchemy 2.x session factory bound to an explicit engine."""

    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def init_database(engine: Engine) -> None:
    """初始化新库，或按版本升级现有 V1 数据库。"""

    from app.persistence import models

    existing_tables = set(inspect(engine).get_table_names())
    if "research_tasks" not in existing_tables:
        Base.metadata.create_all(bind=engine)
        _apply_v1_compatibility_migration(engine)
        _record_migration(engine, 1, "v1_baseline")
        _record_migration(engine, 2, "v2_call_ledger")
        _record_migration(engine, 3, "v2_research_progress")
        _record_migration(engine, 4, "v2_source_snapshot_metadata")
        _record_migration(engine, 5, "v2_report_section_review")
        _record_migration(engine, 6, "v2_review_gap_requests")
        _record_migration(engine, 7, "v2_resume_leases_and_budget_reservations")
        _record_migration(engine, 8, "v2_research_decisions_and_operation_replay")
        return

    models.SchemaMigrationModel.__table__.create(bind=engine, checkfirst=True)
    _apply_v1_compatibility_migration(engine)
    _record_migration(engine, 1, "v1_baseline")

    if get_schema_version(engine) < 2:
        models.CallAttemptModel.__table__.create(bind=engine, checkfirst=True)
        _record_migration(engine, 2, "v2_call_ledger")

    if get_schema_version(engine) < 3:
        models.ResearchProgressModel.__table__.create(bind=engine, checkfirst=True)
        models.ResearchStepModel.__table__.create(bind=engine, checkfirst=True)
        models.QuestionEvidenceModel.__table__.create(bind=engine, checkfirst=True)
        _backfill_question_evidence(engine)
        _record_migration(engine, 3, "v2_research_progress")

    if get_schema_version(engine) < 4:
        _apply_source_metadata_migration(engine)
        _record_migration(engine, 4, "v2_source_snapshot_metadata")

    if get_schema_version(engine) < 5:
        _apply_report_review_migration(engine)
        _record_migration(engine, 5, "v2_report_section_review")

    if get_schema_version(engine) < 6:
        _apply_gap_request_migration(engine)
        _record_migration(engine, 6, "v2_review_gap_requests")

    if get_schema_version(engine) < 7:
        _apply_resume_migration(engine)
        _record_migration(engine, 7, "v2_resume_leases_and_budget_reservations")

    if get_schema_version(engine) < 8:
        models.ResearchDecisionModel.__table__.create(bind=engine, checkfirst=True)
        _apply_research_operation_migration(engine)
        _record_migration(engine, 8, "v2_research_decisions_and_operation_replay")


def get_schema_version(engine: Engine) -> int:
    """返回当前已提交的数据库迁移版本。"""

    if "schema_migrations" not in inspect(engine).get_table_names():
        return 0
    with engine.connect() as connection:
        value = connection.scalar(text("SELECT MAX(version) FROM schema_migrations"))
    return int(value or 0)


def _record_migration(engine: Engine, version: int, name: str) -> None:
    from app.persistence.models import SchemaMigrationModel

    with engine.begin() as connection:
        existing = connection.scalar(
            select(SchemaMigrationModel.version).where(
                SchemaMigrationModel.version == version
            )
        )
        if existing is None:
            connection.execute(
                SchemaMigrationModel.__table__.insert().values(
                    version=version,
                    name=name,
                    applied_at=datetime.now(timezone.utc),
                )
            )


def _apply_v1_compatibility_migration(engine: Engine) -> None:
    if engine.dialect.name != "sqlite":
        return

    task_columns = {
        item["name"] for item in inspect(engine).get_columns("research_tasks")
    }
    with engine.begin() as connection:
        if "execution_stats" not in task_columns:
            connection.execute(
                text(
                    "ALTER TABLE research_tasks ADD COLUMN "
                    "execution_stats JSON NOT NULL DEFAULT '{}'"
                )
            )
        if "latency_ms" not in task_columns:
            connection.execute(
                text(
                    "ALTER TABLE research_tasks ADD COLUMN "
                    "latency_ms FLOAT NOT NULL DEFAULT 0"
                )
            )

    columns = {
        item["name"] for item in inspect(engine).get_columns("workflow_events")
    }
    if "sequence" in columns:
        return
    with engine.begin() as connection:
        connection.execute(
            text("ALTER TABLE workflow_events ADD COLUMN sequence INTEGER")
        )
        connection.execute(
            text(
                "UPDATE workflow_events SET sequence = rowid "
                "WHERE sequence IS NULL"
            )
        )
        connection.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS "
                "ux_workflow_events_sequence ON workflow_events(sequence)"
            )
        )


def _backfill_question_evidence(engine: Engine) -> None:
    """Migrate each legacy EvidenceCard.question_id into the V2 join table."""

    from app.persistence.models import EvidenceCardModel, QuestionEvidenceModel

    with engine.begin() as connection:
        rows = connection.execute(
            select(
                EvidenceCardModel.task_id,
                EvidenceCardModel.question_id,
                EvidenceCardModel.evidence_id,
            )
        ).all()
        if rows:
            connection.execute(
                QuestionEvidenceModel.__table__.insert(),
                [
                    {
                        "task_id": row.task_id,
                        "question_id": row.question_id,
                        "evidence_id": row.evidence_id,
                        "created_at": datetime.now(timezone.utc),
                    }
                    for row in rows
                ],
            )


def _apply_source_metadata_migration(engine: Engine) -> None:
    if engine.dialect.name != "sqlite":
        return

    columns = {
        item["name"] for item in inspect(engine).get_columns("source_records")
    }
    additions = {
        "url_fragment": "TEXT",
        "content_type": "VARCHAR(255)",
        "extraction_version": "VARCHAR(64)",
        "published_at": "DATETIME",
        "source_updated_at": "DATETIME",
        "source_type_reason": "TEXT",
    }
    with engine.begin() as connection:
        for name, column_type in additions.items():
            if name not in columns:
                connection.execute(
                    text(
                        f"ALTER TABLE source_records ADD COLUMN {name} {column_type}"
                    )
                )


def _apply_report_review_migration(engine: Engine) -> None:
    if engine.dialect.name != "sqlite":
        return
    columns = {item["name"] for item in inspect(engine).get_columns("reviews")}
    if "report_section_results" not in columns:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "ALTER TABLE reviews ADD COLUMN "
                    "report_section_results JSON NOT NULL DEFAULT '[]'"
                )
            )


def _apply_gap_request_migration(engine: Engine) -> None:
    if engine.dialect.name != "sqlite":
        return
    columns = {item["name"] for item in inspect(engine).get_columns("reviews")}
    if "gap_requests" not in columns:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "ALTER TABLE reviews ADD COLUMN "
                    "gap_requests JSON NOT NULL DEFAULT '[]'"
                )
            )


def _apply_resume_migration(engine: Engine) -> None:
    """Add persistent run leases and conservative token reservations."""

    if engine.dialect.name != "sqlite":
        return
    task_columns = {
        item["name"] for item in inspect(engine).get_columns("research_tasks")
    }
    attempt_columns = {
        item["name"] for item in inspect(engine).get_columns("call_attempts")
    }
    with engine.begin() as connection:
        if "run_owner" not in task_columns:
            connection.execute(
                text("ALTER TABLE research_tasks ADD COLUMN run_owner VARCHAR(128)")
            )
        if "lease_expires_at" not in task_columns:
            connection.execute(
                text("ALTER TABLE research_tasks ADD COLUMN lease_expires_at DATETIME")
            )
        if "run_attempt" not in task_columns:
            connection.execute(
                text(
                    "ALTER TABLE research_tasks ADD COLUMN "
                    "run_attempt INTEGER NOT NULL DEFAULT 0"
                )
            )
        if "reserved_prompt_tokens" not in attempt_columns:
            connection.execute(
                text(
                    "ALTER TABLE call_attempts ADD COLUMN "
                    "reserved_prompt_tokens INTEGER"
                )
            )
        if "reserved_completion_tokens" not in attempt_columns:
            connection.execute(
                text(
                    "ALTER TABLE call_attempts ADD COLUMN "
                    "reserved_completion_tokens INTEGER"
                )
            )
        if "budget_phase" not in attempt_columns:
            connection.execute(
                text(
                    "ALTER TABLE call_attempts ADD COLUMN budget_phase VARCHAR(64)"
                )
            )


def _apply_research_operation_migration(engine: Engine) -> None:
    if engine.dialect.name != "sqlite":
        return
    columns = {
        item["name"] for item in inspect(engine).get_columns("research_steps")
    }
    additions = {
        "decision_id": "VARCHAR(128)",
        "turn_index": "INTEGER NOT NULL DEFAULT 0",
        "operation_index": "INTEGER NOT NULL DEFAULT 0",
        "tool_name": "VARCHAR(64)",
        "arguments": "JSON",
        "operation_status": "VARCHAR(32) NOT NULL DEFAULT 'committed'",
        "result_payload": "JSON",
        "uncertainty_reason": "TEXT",
        "updated_at": "DATETIME",
    }
    with engine.begin() as connection:
        for name, column_type in additions.items():
            if name not in columns:
                connection.execute(
                    text(
                        f"ALTER TABLE research_steps ADD COLUMN {name} {column_type}"
                    )
                )
        connection.execute(
            text(
                "UPDATE research_steps SET updated_at = created_at "
                "WHERE updated_at IS NULL"
            )
        )
