from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select

from app.database.database import session_scope
from app.database.models import (
    AgentEvidence,
    AgentRun,
    AgentStep,
    AgentToolCall,
    ConversationContextSummary,
    Document,
    OutreachDraft,
    ProfileVersion,
    ResumeRevisionChange,
    ResumeRevisionDraft,
    SearchSession,
    SearchSourceProgress,
    UserConnection,
)


class AgentRepositoryMixin:
    """Additive user-scoped persistence used by the Career Agent."""

    session_factory: Any

    def get_or_create_search_session(
        self,
        user_id: str,
        run_id: str,
        *,
        intent: str,
        normalized_request: dict[str, Any],
        requested_count: int,
        source_call_budget: int,
        max_iterations: int = 4,
    ) -> dict[str, Any]:
        """Resume an equivalent active search instead of restarting providers."""
        with session_scope(self.session_factory) as session:
            run = self._owned_run(session, user_id, run_id)
            item = session.scalar(
                select(SearchSession)
                .where(
                    SearchSession.run_id == run_id,
                    SearchSession.user_id == user_id,
                    SearchSession.intent == intent,
                    SearchSession.status == "active",
                )
                .order_by(SearchSession.created_at.desc())
            )
            if item is not None and dict(item.normalized_request) != dict(normalized_request):
                item = None
            if item is None:
                item = SearchSession(
                    run=run,
                    user=run.user,
                    intent=intent,
                    normalized_request=dict(normalized_request),
                    requested_count=requested_count,
                    max_iterations=max_iterations,
                    source_call_budget=source_call_budget,
                    remaining_source_budget=source_call_budget,
                )
                session.add(item)
                session.flush()
            return self._search_session_dict(item)

    def reserve_search_source_calls(
        self, user_id: str, search_session_id: str, requested_calls: int = 1
    ) -> dict[str, Any]:
        """Reserve budget before network I/O so concurrent tools cannot overspend."""
        requested_calls = max(0, int(requested_calls))
        with session_scope(self.session_factory) as session:
            item = session.scalar(
                select(SearchSession)
                .where(
                    SearchSession.search_session_id == search_session_id,
                    SearchSession.user_id == user_id,
                )
                .with_for_update()
            )
            if item is None:
                raise ValueError("Search session was not found for this user.")
            reserved = min(requested_calls, item.remaining_source_budget)
            item.remaining_source_budget -= reserved
            item.source_calls_used += reserved
            session.flush()
            result = self._search_session_dict(item)
            result["reserved_calls"] = reserved
            return result

    def update_search_session(
        self, user_id: str, search_session_id: str, **updates: Any
    ) -> dict[str, Any]:
        allowed = {
            "iteration", "consecutive_no_progress", "visited_sources",
            "provider_cursors", "seen_candidate_ids", "query_variants",
            "source_failures", "source_coverage", "candidate_records", "status",
        }
        unknown = set(updates) - allowed
        if unknown:
            raise ValueError(f"Unsupported search-session fields: {sorted(unknown)}")
        with session_scope(self.session_factory) as session:
            item = session.scalar(
                select(SearchSession).where(
                    SearchSession.search_session_id == search_session_id,
                    SearchSession.user_id == user_id,
                )
            )
            if item is None:
                raise ValueError("Search session was not found for this user.")
            for key, value in updates.items():
                setattr(item, key, value)
            session.flush()
            return self._search_session_dict(item)

    def upsert_search_source_progress(
        self,
        user_id: str,
        search_session_id: str,
        *,
        source_key: str,
        provider: str,
        query_hash: str,
        **updates: Any,
    ) -> dict[str, Any]:
        allowed = {
            "company_or_domain", "visited", "cursor", "next_cursor", "has_more",
            "exhausted", "call_count", "first_iteration", "last_iteration",
            "last_success_at", "last_error_type",
        }
        with session_scope(self.session_factory) as session:
            search = session.scalar(
                select(SearchSession).where(
                    SearchSession.search_session_id == search_session_id,
                    SearchSession.user_id == user_id,
                )
            )
            if search is None:
                raise ValueError("Search session was not found for this user.")
            item = session.scalar(
                select(SearchSourceProgress).where(
                    SearchSourceProgress.search_session_id == search_session_id,
                    SearchSourceProgress.source_key == source_key,
                )
            )
            if item is None:
                item = SearchSourceProgress(
                    search_session=search,
                    source_key=source_key,
                    provider=provider,
                    query_hash=query_hash,
                )
                session.add(item)
            for key, value in updates.items():
                if key not in allowed:
                    raise ValueError(f"Unsupported source-progress field: {key}")
                setattr(item, key, value)
            session.flush()
            return self._source_progress_dict(item)

    def create_agent_run(
        self,
        user_id: str,
        conversation_id: str,
        *,
        goal: str,
        user_message_id: str | None = None,
    ) -> dict[str, Any]:
        with session_scope(self.session_factory) as session:
            user = self._require_user(session, user_id)
            conversation = self._owned_conversation(session, user_id, conversation_id)
            if user_message_id is not None and not any(
                item.message_id == user_message_id and item.role == "user"
                for item in conversation.messages
            ):
                raise ValueError("Agent run user message was not found in this conversation.")
            run = AgentRun(
                user=user,
                conversation=conversation,
                goal=goal.strip(),
                user_message_id=user_message_id,
            )
            session.add(run)
            session.flush()
            return self._agent_run_dict(run)

    def update_agent_run(
        self,
        user_id: str,
        run_id: str,
        *,
        intent: str | None = None,
        goal: str | None = None,
        status: str | None = None,
        final_summary: str | None = None,
        error_summary: str | None = None,
        assistant_message_id: str | None = None,
        state_json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with session_scope(self.session_factory) as session:
            run = self._owned_run(session, user_id, run_id)
            if intent is not None:
                run.intent = intent
            if goal is not None:
                run.goal = goal.strip()
            if status is not None:
                if status not in {
                    "running", "completed", "failed", "needs_input", "partial"
                }:
                    raise ValueError("Invalid agent run status.")
                run.status = status
                if status in {"completed", "failed", "partial"}:
                    run.completed_at = datetime.now(timezone.utc)
            if final_summary is not None:
                run.final_summary = final_summary
            if error_summary is not None:
                run.error_summary = error_summary
            if assistant_message_id is not None:
                message = next(
                    (
                        item
                        for item in run.conversation.messages
                        if item.message_id == assistant_message_id
                        and item.role == "assistant"
                    ),
                    None,
                )
                if message is None:
                    raise ValueError(
                        "Agent run assistant message was not found in this conversation."
                    )
                run.assistant_message_id = assistant_message_id
            if state_json is not None:
                run.state_json = dict(state_json)
            session.flush()
            return self._agent_run_dict(run)

    def create_agent_step(
        self,
        user_id: str,
        run_id: str,
        *,
        stage: str,
        status: str,
        display_summary: str,
    ) -> dict[str, Any]:
        with session_scope(self.session_factory) as session:
            run = self._owned_run(session, user_id, run_id)
            sequence = (
                session.scalar(
                    select(func.max(AgentStep.sequence_number)).where(
                        AgentStep.run_id == run_id
                    )
                )
                or 0
            ) + 1
            step = AgentStep(
                run=run,
                sequence_number=sequence,
                stage=self._required_text(stage, "stage"),
                status=self._required_text(status, "status"),
                display_summary=self._required_text(display_summary, "display_summary"),
            )
            if status in {"completed", "failed"}:
                step.completed_at = datetime.now(timezone.utc)
            session.add(step)
            session.flush()
            return self._agent_step_dict(step)

    def record_agent_tool_call(
        self,
        user_id: str,
        run_id: str,
        *,
        tool_call_id: str,
        tool_name: str,
        sanitized_arguments: dict[str, Any],
        status: str,
        result_summary: str | None = None,
        error_type: str | None = None,
        error_message: str | None = None,
        duration_ms: int | None = None,
        step_id: str | None = None,
    ) -> dict[str, Any]:
        with session_scope(self.session_factory) as session:
            run = self._owned_run(session, user_id, run_id)
            if step_id is not None:
                step = session.scalar(
                    select(AgentStep).where(
                        AgentStep.step_id == step_id, AgentStep.run_id == run_id
                    )
                )
                if step is None:
                    raise ValueError("Agent step was not found for this run.")
            call_number = (
                session.scalar(
                    select(func.count(AgentToolCall.tool_call_id)).where(
                        AgentToolCall.run_id == run_id,
                        AgentToolCall.tool_name == tool_name,
                    )
                )
                or 0
            ) + 1
            call = AgentToolCall(
                tool_call_id=tool_call_id,
                run=run,
                step_id=step_id,
                tool_name=tool_name,
                sanitized_arguments_json=dict(sanitized_arguments),
                result_summary=result_summary,
                status=status,
                error_type=error_type,
                error_message=error_message,
                call_number=call_number,
                duration_ms=duration_ms,
            )
            session.add(call)
            session.flush()
            return self._agent_tool_call_dict(call)

    def list_agent_runs(self, user_id: str, conversation_id: str) -> list[dict[str, Any]]:
        with session_scope(self.session_factory) as session:
            self._owned_conversation(session, user_id, conversation_id)
            runs = session.scalars(
                select(AgentRun)
                .where(
                    AgentRun.user_id == user_id,
                    AgentRun.conversation_id == conversation_id,
                )
                .order_by(AgentRun.started_at.desc())
            ).all()
            return [self._agent_run_dict(run, include_children=True) for run in runs]

    def create_evidence(
        self,
        user_id: str,
        run_id: str,
        *,
        evidence_id: str,
        source_type: str,
        source_name: str,
        source_url: str | None,
        content_type: str,
        content_excerpt: str,
        structured_content: dict[str, Any] | None,
        content_hash: str,
        raw_content: str | None,
        raw_size_bytes: int,
        storage_backend: str,
        storage_key: str | None,
    ) -> dict[str, Any]:
        with session_scope(self.session_factory) as session:
            run = self._owned_run(session, user_id, run_id)
            evidence = AgentEvidence(
                evidence_id=evidence_id,
                user=run.user,
                run=run,
                source_type=source_type,
                source_name=source_name,
                source_url=source_url,
                content_type=content_type,
                content_excerpt=content_excerpt,
                structured_content=structured_content,
                content_hash=content_hash,
                raw_content=raw_content,
                raw_size_bytes=raw_size_bytes,
                storage_backend=storage_backend,
                storage_key=storage_key,
            )
            session.add(evidence)
            session.flush()
            return self._evidence_dict(evidence)

    def get_evidence(self, user_id: str, evidence_id: str) -> dict[str, Any]:
        with session_scope(self.session_factory) as session:
            evidence = session.scalar(
                select(AgentEvidence).where(
                    AgentEvidence.evidence_id == evidence_id,
                    AgentEvidence.user_id == user_id,
                )
            )
            if evidence is None:
                raise ValueError("Evidence was not found for this user.")
            return self._evidence_dict(evidence)

    def save_context_summary(
        self,
        user_id: str,
        conversation_id: str,
        *,
        summary: str,
        covered_through_message_id: str,
        evidence_ids: list[str],
        strategy: str,
    ) -> dict[str, Any]:
        with session_scope(self.session_factory) as session:
            conversation = self._owned_conversation(session, user_id, conversation_id)
            if not any(item.message_id == covered_through_message_id for item in conversation.messages):
                raise ValueError("Summary boundary was not found in this conversation.")
            item = ConversationContextSummary(
                conversation=conversation,
                summary=self._required_text(summary, "summary"),
                covered_through_message_id=covered_through_message_id,
                evidence_ids=list(dict.fromkeys(evidence_ids)),
                strategy=self._required_text(strategy, "strategy"),
            )
            session.add(item)
            session.flush()
            return self._context_summary_dict(item)

    def get_latest_context_summary(
        self, user_id: str, conversation_id: str
    ) -> dict[str, Any] | None:
        with session_scope(self.session_factory) as session:
            self._owned_conversation(session, user_id, conversation_id)
            item = session.scalar(
                select(ConversationContextSummary)
                .where(ConversationContextSummary.conversation_id == conversation_id)
                .order_by(ConversationContextSummary.created_at.desc())
            )
            return self._context_summary_dict(item) if item else None

    def create_connection(self, user_id: str, data: dict[str, Any]) -> dict[str, Any]:
        with session_scope(self.session_factory) as session:
            user = self._require_user(session, user_id)
            item = UserConnection(
                user=user,
                name=self._required_text(data.get("name"), "name")[:300],
                current_role=self._clean_optional(data.get("current_role")),
                organization=self._clean_optional(data.get("organization")),
                education=self._clean_optional(data.get("education")),
                graduation_year=int(data["graduation_year"]) if data.get("graduation_year") else None,
                public_profile_url=self._clean_optional(data.get("public_profile_url")),
                user_provided_email=self._clean_optional(data.get("user_provided_email")),
                notes=self._clean_optional(data.get("notes")),
                source_type=self._required_text(data.get("source_type") or "manual", "source_type"),
            )
            session.add(item)
            session.flush()
            return self._connection_dict(item)

    def list_connections(self, user_id: str) -> list[dict[str, Any]]:
        with session_scope(self.session_factory) as session:
            self._require_user(session, user_id)
            items = session.scalars(
                select(UserConnection)
                .where(UserConnection.user_id == user_id)
                .order_by(UserConnection.created_at.desc())
            ).all()
            return [self._connection_dict(item) for item in items]

    def get_connection(self, user_id: str, connection_id: str) -> dict[str, Any]:
        with session_scope(self.session_factory) as session:
            item = session.scalar(
                select(UserConnection).where(
                    UserConnection.connection_id == connection_id,
                    UserConnection.user_id == user_id,
                )
            )
            if item is None:
                raise ValueError("Connection was not found for this user.")
            return self._connection_dict(item)

    def save_resume_revision_draft(
        self, user_id: str, data: dict[str, Any]
    ) -> dict[str, Any]:
        with session_scope(self.session_factory) as session:
            user = self._require_user(session, user_id)
            version = session.scalar(
                select(ProfileVersion).where(
                    ProfileVersion.version_id == data.get("source_profile_version_id"),
                    ProfileVersion.user_id == user_id,
                )
            )
            if version is None:
                raise ValueError("Profile version was not found for this user.")
            source_ids = {str(item) for item in data.get("source_document_ids") or []}
            if source_ids:
                owned = set(
                    session.scalars(
                        select(Document.document_id).where(
                            Document.user_id == user_id, Document.document_id.in_(source_ids)
                        )
                    ).all()
                )
                if owned != source_ids:
                    raise ValueError("Every resume source must belong to this user.")
            draft = ResumeRevisionDraft(
                user=user,
                source_profile_version_id=version.version_id,
                source_document_ids=sorted(source_ids),
                target_job_ids=list(dict.fromkeys(data.get("target_job_ids") or [])),
                template_id=self._clean_optional(data.get("template_id")),
                summary=self._required_text(data.get("summary"), "summary"),
                status="draft",
            )
            for change in data.get("changes") or []:
                draft.changes.append(
                    ResumeRevisionChange(
                        section=self._required_text(change.get("section"), "section"),
                        entry_identifier=self._clean_optional(change.get("entry_identifier")),
                        original_text=self._clean_optional(change.get("original_text")),
                        proposed_text=self._required_text(change.get("proposed_text"), "proposed_text"),
                        rationale=self._required_text(change.get("rationale"), "rationale"),
                        profile_evidence_ids=list(dict.fromkeys(change.get("profile_evidence_ids") or [])),
                        job_evidence_ids=list(dict.fromkeys(change.get("job_evidence_ids") or [])),
                        warnings=list(change.get("warnings") or []),
                    )
                )
            if not draft.changes:
                raise ValueError("At least one resume change is required.")
            session.add(draft)
            session.flush()
            return self._resume_draft_dict(draft)

    def list_resume_revision_drafts(self, user_id: str) -> list[dict[str, Any]]:
        with session_scope(self.session_factory) as session:
            self._require_user(session, user_id)
            items = session.scalars(
                select(ResumeRevisionDraft)
                .where(ResumeRevisionDraft.user_id == user_id)
                .order_by(ResumeRevisionDraft.created_at.desc())
            ).all()
            return [self._resume_draft_dict(item) for item in items]

    def save_outreach_draft(self, user_id: str, data: dict[str, Any]) -> dict[str, Any]:
        with session_scope(self.session_factory) as session:
            user = self._require_user(session, user_id)
            previous_id = data.get("previous_draft_id")
            previous = None
            is_follow_up = data.get("outreach_type") == "no_response_follow_up"
            if is_follow_up and not previous_id:
                raise ValueError(
                    "A no-response follow-up requires a previous outreach draft."
                )
            if previous_id:
                previous = session.scalar(
                    select(OutreachDraft).where(
                        OutreachDraft.draft_id == previous_id,
                        OutreachDraft.user_id == user_id,
                    )
                )
                if previous is None:
                    raise ValueError("Previous outreach draft was not found for this user.")
                if is_follow_up and previous.sent_at is None:
                    raise ValueError("A follow-up requires a previous draft marked sent.")
                if is_follow_up and (
                    previous.recipient_name.casefold()
                    != self._required_text(
                        data.get("recipient_name"), "recipient_name"
                    ).casefold()
                ):
                    raise ValueError(
                        "A follow-up recipient must match the previous outreach draft."
                    )
            draft = OutreachDraft(
                user=user,
                outreach_type=self._required_text(data.get("outreach_type"), "outreach_type"),
                recipient_candidate_id=self._clean_optional(data.get("recipient_candidate_id")),
                recipient_name=self._required_text(data.get("recipient_name"), "recipient_name"),
                recipient_role=self._clean_optional(data.get("recipient_role")),
                recipient_organization=self._clean_optional(data.get("recipient_organization")),
                subject=self._required_text(data.get("subject"), "subject")[:500],
                body=self._required_text(data.get("body"), "body"),
                relevant_connections=list(data.get("relevant_connections") or []),
                evidence_ids=list(dict.fromkeys(data.get("evidence_ids") or [])),
                previous_draft_id=previous.draft_id if previous else None,
                status="draft",
            )
            session.add(draft)
            session.flush()
            return self._outreach_draft_dict(draft)

    def update_outreach_status(
        self, user_id: str, draft_id: str, status: str, *, explicit_user_action: bool
    ) -> dict[str, Any]:
        transitions = {
            "draft": {"ready", "archived"},
            "ready": {"draft", "sent", "archived"},
            "sent": {"archived"},
            "archived": set(),
        }
        if status == "sent" and not explicit_user_action:
            raise ValueError("Marking outreach sent requires explicit user action.")
        with session_scope(self.session_factory) as session:
            draft = session.scalar(
                select(OutreachDraft).where(
                    OutreachDraft.draft_id == draft_id,
                    OutreachDraft.user_id == user_id,
                )
            )
            if draft is None:
                raise ValueError("Outreach draft was not found for this user.")
            if status not in transitions.get(draft.status, set()):
                raise ValueError(f"Invalid outreach transition: {draft.status} -> {status}")
            draft.status = status
            if status == "sent":
                draft.sent_at = datetime.now(timezone.utc)
            session.flush()
            return self._outreach_draft_dict(draft)

    def list_outreach_drafts(self, user_id: str) -> list[dict[str, Any]]:
        with session_scope(self.session_factory) as session:
            self._require_user(session, user_id)
            items = session.scalars(
                select(OutreachDraft)
                .where(OutreachDraft.user_id == user_id)
                .order_by(OutreachDraft.created_at.desc())
            ).all()
            return [self._outreach_draft_dict(item) for item in items]

    @staticmethod
    def _owned_run(session, user_id: str, run_id: str) -> AgentRun:
        run = session.scalar(select(AgentRun).where(AgentRun.run_id == run_id, AgentRun.user_id == user_id))
        if run is None:
            raise ValueError("Agent run was not found for this user.")
        return run

    @staticmethod
    def _agent_run_dict(run: AgentRun, include_children: bool = False) -> dict[str, Any]:
        result = {
            "run_id": run.run_id, "user_id": run.user_id,
            "conversation_id": run.conversation_id, "intent": run.intent,
            "goal": run.goal, "status": run.status,
            "started_at": run.started_at.isoformat(),
            "completed_at": run.completed_at.isoformat() if run.completed_at else None,
            "final_summary": run.final_summary, "error_summary": run.error_summary,
            "user_message_id": run.user_message_id,
            "assistant_message_id": run.assistant_message_id,
            "state": dict(run.state_json or {}),
        }
        if include_children:
            result["steps"] = [AgentRepositoryMixin._agent_step_dict(item) for item in sorted(run.steps, key=lambda x: x.sequence_number)]
            result["tool_calls"] = [AgentRepositoryMixin._agent_tool_call_dict(item) for item in sorted(run.tool_calls, key=lambda x: x.created_at)]
        return result

    @staticmethod
    def _search_session_dict(item: SearchSession) -> dict[str, Any]:
        return {
            "search_session_id": item.search_session_id,
            "run_id": item.run_id,
            "user_id": item.user_id,
            "intent": item.intent,
            "normalized_request": dict(item.normalized_request),
            "requested_count": item.requested_count,
            "iteration": item.iteration,
            "max_iterations": item.max_iterations,
            "source_call_budget": item.source_call_budget,
            "source_calls_used": item.source_calls_used,
            "remaining_source_budget": item.remaining_source_budget,
            "consecutive_no_progress": item.consecutive_no_progress,
            "visited_sources": list(item.visited_sources),
            "provider_cursors": dict(item.provider_cursors),
            "seen_candidate_ids": list(item.seen_candidate_ids),
            "candidate_records": list(item.candidate_records),
            "query_variants": list(item.query_variants),
            "source_failures": dict(item.source_failures),
            "source_coverage": dict(item.source_coverage),
            "status": item.status,
            "sources": [AgentRepositoryMixin._source_progress_dict(source) for source in item.sources],
        }

    @staticmethod
    def _source_progress_dict(item: SearchSourceProgress) -> dict[str, Any]:
        return {
            "source_key": item.source_key,
            "provider": item.provider,
            "company_or_domain": item.company_or_domain,
            "query_hash": item.query_hash,
            "visited": item.visited,
            "cursor": item.cursor,
            "next_cursor": item.next_cursor,
            "has_more": item.has_more,
            "exhausted": item.exhausted,
            "call_count": item.call_count,
            "first_iteration": item.first_iteration,
            "last_iteration": item.last_iteration,
            "last_success_at": item.last_success_at.isoformat() if item.last_success_at else None,
            "last_error_type": item.last_error_type,
        }

    @staticmethod
    def _agent_step_dict(item: AgentStep) -> dict[str, Any]:
        return {"step_id": item.step_id, "run_id": item.run_id, "sequence_number": item.sequence_number, "stage": item.stage, "status": item.status, "display_summary": item.display_summary, "started_at": item.started_at.isoformat(), "completed_at": item.completed_at.isoformat() if item.completed_at else None}

    @staticmethod
    def _agent_tool_call_dict(item: AgentToolCall) -> dict[str, Any]:
        return {"tool_call_id": item.tool_call_id, "run_id": item.run_id, "step_id": item.step_id, "tool_name": item.tool_name, "sanitized_arguments": dict(item.sanitized_arguments_json), "result_summary": item.result_summary, "status": item.status, "error_type": item.error_type, "error_message": item.error_message, "call_number": item.call_number, "duration_ms": item.duration_ms, "created_at": item.created_at.isoformat()}

    @staticmethod
    def _evidence_dict(item: AgentEvidence) -> dict[str, Any]:
        return {"evidence_id": item.evidence_id, "user_id": item.user_id, "run_id": item.run_id, "source_type": item.source_type, "source_name": item.source_name, "source_url": item.source_url, "retrieved_at": item.retrieved_at.isoformat(), "content_type": item.content_type, "content_excerpt": item.content_excerpt, "structured_content": item.structured_content, "content_hash": item.content_hash, "raw_content": item.raw_content, "raw_size_bytes": item.raw_size_bytes, "storage_backend": item.storage_backend, "storage_key": item.storage_key, "created_at": item.created_at.isoformat()}

    @staticmethod
    def _context_summary_dict(item: ConversationContextSummary) -> dict[str, Any]:
        return {"summary_id": item.summary_id, "conversation_id": item.conversation_id, "summary": item.summary, "covered_through_message_id": item.covered_through_message_id, "evidence_ids": list(item.evidence_ids), "strategy": item.strategy, "created_at": item.created_at.isoformat()}

    @staticmethod
    def _connection_dict(item: UserConnection) -> dict[str, Any]:
        return {"connection_id": item.connection_id, "user_id": item.user_id, "name": item.name, "current_role": item.current_role, "organization": item.organization, "education": item.education, "graduation_year": item.graduation_year, "public_profile_url": item.public_profile_url, "user_provided_email": item.user_provided_email, "notes": item.notes, "source_type": item.source_type, "created_at": item.created_at.isoformat(), "updated_at": item.updated_at.isoformat()}

    @staticmethod
    def _resume_draft_dict(item: ResumeRevisionDraft) -> dict[str, Any]:
        return {"draft_id": item.draft_id, "user_id": item.user_id, "source_profile_version_id": item.source_profile_version_id, "source_document_ids": list(item.source_document_ids), "target_job_ids": list(item.target_job_ids), "template_id": item.template_id, "summary": item.summary, "status": item.status, "created_at": item.created_at.isoformat(), "updated_at": item.updated_at.isoformat(), "changes": [{"change_id": change.change_id, "section": change.section, "entry_identifier": change.entry_identifier, "original_text": change.original_text, "proposed_text": change.proposed_text, "rationale": change.rationale, "profile_evidence_ids": list(change.profile_evidence_ids), "job_evidence_ids": list(change.job_evidence_ids), "warnings": list(change.warnings)} for change in item.changes]}

    @staticmethod
    def _outreach_draft_dict(item: OutreachDraft) -> dict[str, Any]:
        return {"draft_id": item.draft_id, "user_id": item.user_id, "outreach_type": item.outreach_type, "recipient_candidate_id": item.recipient_candidate_id, "recipient_name": item.recipient_name, "recipient_role": item.recipient_role, "recipient_organization": item.recipient_organization, "subject": item.subject, "body": item.body, "relevant_connections": list(item.relevant_connections), "evidence_ids": list(item.evidence_ids), "previous_draft_id": item.previous_draft_id, "sent_at": item.sent_at.isoformat() if item.sent_at else None, "status": item.status, "created_at": item.created_at.isoformat(), "updated_at": item.updated_at.isoformat()}
