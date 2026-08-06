"""Add Career Agent trajectories, evidence, connections, and drafts.

Revision ID: 20260806_05
Revises: 20260803_04
"""

from alembic import op
import sqlalchemy as sa

revision = "20260806_05"
down_revision = "20260803_04"
branch_labels = None
depends_on = None


def upgrade() -> None:
    required_tables = {
        "agent_runs",
        "agent_steps",
        "agent_tool_calls",
        "agent_evidence",
        "conversation_context_summaries",
        "user_connections",
        "resume_revision_drafts",
        "resume_revision_changes",
        "outreach_drafts",
    }
    existing = set(sa.inspect(op.get_bind()).get_table_names())
    # The repository's historical bootstrap migration creates current metadata
    # for a brand-new database. Existing deployments at the previous revision do
    # not have these tables and continue through the explicit DDL below.
    if required_tables <= existing:
        return
    partial = required_tables & existing
    if partial:
        raise RuntimeError(
            "Career Agent migration found a partial schema: "
            + ", ".join(sorted(partial))
        )
    op.create_table(
        "agent_runs",
        sa.Column("run_id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False),
        sa.Column("conversation_id", sa.String(36), sa.ForeignKey("conversations.conversation_id", ondelete="CASCADE"), nullable=False),
        sa.Column("intent", sa.String(50), nullable=True),
        sa.Column("goal", sa.Text(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("final_summary", sa.Text(), nullable=True),
        sa.Column("error_summary", sa.Text(), nullable=True),
    )
    for column in ("user_id", "conversation_id", "intent", "status", "started_at"):
        op.create_index(f"ix_agent_runs_{column}", "agent_runs", [column])

    op.create_table(
        "agent_steps",
        sa.Column("step_id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("agent_runs.run_id", ondelete="CASCADE"), nullable=False),
        sa.Column("sequence_number", sa.Integer(), nullable=False),
        sa.Column("stage", sa.String(100), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("display_summary", sa.Text(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("run_id", "sequence_number", name="uq_agent_step_run_sequence"),
    )
    for column in ("run_id", "stage", "status"):
        op.create_index(f"ix_agent_steps_{column}", "agent_steps", [column])

    op.create_table(
        "agent_tool_calls",
        sa.Column("tool_call_id", sa.String(100), primary_key=True),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("agent_runs.run_id", ondelete="CASCADE"), nullable=False),
        sa.Column("step_id", sa.String(36), sa.ForeignKey("agent_steps.step_id", ondelete="SET NULL"), nullable=True),
        sa.Column("tool_name", sa.String(100), nullable=False),
        sa.Column("sanitized_arguments_json", sa.JSON(), nullable=False),
        sa.Column("result_summary", sa.Text(), nullable=True),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("error_type", sa.String(100), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("call_number", sa.Integer(), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    for column in ("run_id", "step_id", "tool_name", "status", "created_at"):
        op.create_index(f"ix_agent_tool_calls_{column}", "agent_tool_calls", [column])

    op.create_table(
        "agent_evidence",
        sa.Column("evidence_id", sa.String(39), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("agent_runs.run_id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_type", sa.String(50), nullable=False),
        sa.Column("source_name", sa.String(200), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("content_type", sa.String(100), nullable=False),
        sa.Column("content_excerpt", sa.Text(), nullable=False),
        sa.Column("structured_content", sa.JSON(), nullable=True),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("raw_content", sa.Text(), nullable=True),
        sa.Column("raw_size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("storage_backend", sa.String(20), nullable=False),
        sa.Column("storage_key", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    for column in ("user_id", "run_id", "retrieved_at", "content_hash", "created_at"):
        op.create_index(f"ix_agent_evidence_{column}", "agent_evidence", [column])

    op.create_table(
        "conversation_context_summaries",
        sa.Column("summary_id", sa.String(36), primary_key=True),
        sa.Column("conversation_id", sa.String(36), sa.ForeignKey("conversations.conversation_id", ondelete="CASCADE"), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("covered_through_message_id", sa.String(36), sa.ForeignKey("messages.message_id", ondelete="RESTRICT"), nullable=False),
        sa.Column("evidence_ids", sa.JSON(), nullable=False),
        sa.Column("strategy", sa.String(100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    for column in ("conversation_id", "covered_through_message_id", "created_at"):
        op.create_index(f"ix_conversation_context_summaries_{column}", "conversation_context_summaries", [column])

    op.create_table(
        "user_connections",
        sa.Column("connection_id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(300), nullable=False),
        sa.Column("current_role", sa.String(300), nullable=True),
        sa.Column("organization", sa.String(300), nullable=True),
        sa.Column("education", sa.Text(), nullable=True),
        sa.Column("graduation_year", sa.Integer(), nullable=True),
        sa.Column("public_profile_url", sa.Text(), nullable=True),
        sa.Column("user_provided_email", sa.String(320), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("source_type", sa.String(50), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_user_connections_user_id", "user_connections", ["user_id"])
    op.create_index("ix_user_connections_organization", "user_connections", ["organization"])

    op.create_table(
        "resume_revision_drafts",
        sa.Column("draft_id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_profile_version_id", sa.String(36), sa.ForeignKey("profile_versions.version_id", ondelete="RESTRICT"), nullable=False),
        sa.Column("source_document_ids", sa.JSON(), nullable=False),
        sa.Column("target_job_ids", sa.JSON(), nullable=False),
        sa.Column("template_id", sa.String(100), nullable=True),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    for column in ("user_id", "source_profile_version_id", "status"):
        op.create_index(f"ix_resume_revision_drafts_{column}", "resume_revision_drafts", [column])

    op.create_table(
        "resume_revision_changes",
        sa.Column("change_id", sa.String(36), primary_key=True),
        sa.Column("draft_id", sa.String(36), sa.ForeignKey("resume_revision_drafts.draft_id", ondelete="CASCADE"), nullable=False),
        sa.Column("section", sa.String(100), nullable=False),
        sa.Column("entry_identifier", sa.String(300), nullable=True),
        sa.Column("original_text", sa.Text(), nullable=True),
        sa.Column("proposed_text", sa.Text(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("profile_evidence_ids", sa.JSON(), nullable=False),
        sa.Column("job_evidence_ids", sa.JSON(), nullable=False),
        sa.Column("warnings", sa.JSON(), nullable=False),
    )
    op.create_index("ix_resume_revision_changes_draft_id", "resume_revision_changes", ["draft_id"])

    op.create_table(
        "outreach_drafts",
        sa.Column("draft_id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False),
        sa.Column("outreach_type", sa.String(50), nullable=False),
        sa.Column("recipient_candidate_id", sa.String(100), nullable=True),
        sa.Column("recipient_name", sa.String(300), nullable=False),
        sa.Column("recipient_role", sa.String(300), nullable=True),
        sa.Column("recipient_organization", sa.String(300), nullable=True),
        sa.Column("subject", sa.String(500), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("relevant_connections", sa.JSON(), nullable=False),
        sa.Column("evidence_ids", sa.JSON(), nullable=False),
        sa.Column("previous_draft_id", sa.String(36), sa.ForeignKey("outreach_drafts.draft_id", ondelete="SET NULL"), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    for column in ("user_id", "outreach_type", "previous_draft_id", "status"):
        op.create_index(f"ix_outreach_drafts_{column}", "outreach_drafts", [column])


def downgrade() -> None:
    for table in (
        "outreach_drafts",
        "resume_revision_changes",
        "resume_revision_drafts",
        "user_connections",
        "conversation_context_summaries",
        "agent_evidence",
        "agent_tool_calls",
        "agent_steps",
        "agent_runs",
    ):
        op.drop_table(table)
