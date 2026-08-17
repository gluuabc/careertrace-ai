from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any

from app.database.repository import ProfileRepository, profile_repository
from app.database.retrieval_repository import RetrievalRepository
from app.services.retrieval import HybridRetrievalService


PROFILE_FIELDS_BY_INTENT: dict[str, tuple[str, ...]] = {
    "job_search": (
        "education", "school", "major", "graduation_year", "skills",
        "experience", "work_authorization", "target_roles",
        "preferred_locations", "employment_types", "remote_preference",
    ),
    "people_search": ("education", "school", "major", "skills", "projects", "experience"),
    "resume_revision": (
        "education", "school", "major", "graduation_year", "career_goal",
        "skills", "courses", "achievements", "certifications", "projects", "experience",
    ),
    "outreach": ("name", "school", "major", "skills", "projects", "experience", "career_goal"),
    "concise_guidance": ("education", "school", "major", "graduation_year", "skills", "projects", "experience", "career_goal", "target_roles"),
    "action_plan": ("education", "school", "major", "graduation_year", "skills", "projects", "experience", "career_goal", "target_roles"),
}

MEMORY_TYPES_BY_INTENT: dict[str, tuple[str, ...]] = {
    "job_search": ("preference", "goal", "constraint", "event"),
    "people_search": ("preference", "goal", "constraint", "event"),
    "resume_revision": ("goal", "constraint", "event"),
    "outreach": ("preference", "goal", "constraint", "event"),
    "action_plan": ("preference", "goal", "constraint", "event"),
    "concise_guidance": ("preference", "goal", "constraint", "event"),
}

FIELD_TERMS: dict[str, tuple[str, ...]] = {
    "school": ("school", "university", "college", "alumni"),
    "major": ("major", "degree", "study", "field"),
    "graduation_year": ("graduate", "graduation", "student", "year"),
    "skills": ("skill", "technology", "qualified", "strength"),
    "experience": ("experience", "work", "intern", "role", "job"),
    "projects": ("project", "portfolio", "built"),
    "education": ("education", "coursework", "degree"),
    "career_goal": ("goal", "career", "future"),
    "work_authorization": ("authorization", "sponsor", "visa", "eligible"),
    "target_roles": ("target role", "role", "job"),
    "preferred_locations": ("location", "city", "where", "remote"),
    "employment_types": ("internship", "full-time", "part-time", "employment"),
    "remote_preference": ("remote", "hybrid", "onsite"),
}


def _tokens(value: str) -> set[str]:
    return {item.casefold() for item in re.findall(r"[a-z0-9+#.]{2,}", value, flags=re.I)}


def _intent_value(intent: Any) -> str:
    return str(getattr(intent, "value", intent) or "concise_guidance")


class ProgressiveMemoryService:
    """Bounded L0-L3 access to exact Profile fields and approved memories."""

    def __init__(
        self,
        repository: ProfileRepository = profile_repository,
        retrieval_service: HybridRetrievalService | None = None,
    ):
        self.repository = repository
        self.retrieval_service = retrieval_service or HybridRetrievalService(
            RetrievalRepository(repository.session_factory)
        )

    def profile_projection(
        self,
        *,
        user_id: str,
        conversation_id: str | None,
        intent: Any,
        query: str,
    ) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
        overlay = (
            self.repository.get_effective_conversation_context(user_id, conversation_id)
            if conversation_id
            else {
                "persisted_profile": self.repository.get_profile(user_id) or {},
                "effective_profile": self.repository.get_profile(user_id) or {},
                "signals": [],
                "current_thread_memories": {},
            }
        )
        profile = dict(overlay.get("effective_profile") or {})
        intent_value = _intent_value(intent)
        fields = list(PROFILE_FIELDS_BY_INTENT.get(intent_value, ()))
        if not fields:
            lowered = query.casefold()
            fields = [
                field for field, terms in FIELD_TERMS.items()
                if any(term in lowered for term in terms)
            ]
        projection = {
            field: profile[field]
            for field in fields
            if field in profile and profile[field] not in (None, "", [], {})
        }
        version_id = (overlay.get("persisted_profile") or {}).get("profile_version_id")
        references = [
            {"profile_version_id": version_id, "field": field, "value": value}
            for field, value in projection.items()
        ]
        return projection, references, overlay

    def memory_catalog(
        self,
        *,
        user_id: str,
        query: str,
        intent: Any,
        current_thread_memories: dict[str, list[str]] | None = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        requested_limit = max(1, min(int(limit), 10))
        lowered = query.casefold()
        semantic_only = any(phrase in lowered for phrase in (
            "what preferences do you remember", "my preferences", "my goals",
            "my constraints", "semantic memory",
        ))
        episodic_only = any(phrase in lowered for phrase in (
            "what did i do", "what happened", "last summer", "career events",
            "episodic memory",
        ))
        overlay_types = {
            key.split(".", 1)[1] for key in (current_thread_memories or {})
            if key.startswith("memory.")
        }
        if semantic_only and not episodic_only:
            corpus_types = ["semantic_memory"]
            memories = self.repository.list_semantic_memories(user_id)
        elif episodic_only and not semantic_only:
            corpus_types = ["episodic_event"]
            memories = self.repository.list_career_events(user_id)
        else:
            corpus_types = ["semantic_memory", "episodic_event", "approved_memory"]
            legacy = [item for item in self.repository.list_memories(user_id) if item["category"] not in overlay_types]
            memories = [
                *self.repository.list_semantic_memories(user_id),
                *self.repository.list_career_events(user_id),
                *legacy,
            ]
        memories = list({item["memory_id"]: item for item in reversed(memories)}.values())
        if not memories:
            return []
        by_id = {item["memory_id"]: item for item in memories}
        query_tokens = _tokens(query)
        rank: dict[str, int] = {}
        try:
            result = self.retrieval_service.retrieve(
                user_id=user_id,
                query=query,
                corpus_types=corpus_types,
                top_k=10,
            )
            for index, hit in enumerate(result.items):
                metadata = getattr(hit, "metadata", {}) or {}
                memory_id = str(metadata.get("semantic_memory_id") or metadata.get("career_event_id") or metadata.get("memory_id") or "")
                if not memory_id:
                    memory_id = next(
                        (
                            item["memory_id"] for item in memories
                            if item["content"].strip() == str(hit.text_excerpt).strip()
                        ),
                        "",
                    )
                if memory_id in by_id and memory_id not in rank:
                    rank[memory_id] = index
        except Exception:
            pass

        def relevance(item: dict[str, Any]) -> tuple[int, int, str]:
            overlap = len(query_tokens & _tokens(f"{item['category']} {item['content']}"))
            semantic_rank = rank.get(item["memory_id"], 1000)
            return (-overlap, semantic_rank, item["created_at"])

        ordered = sorted(memories, key=relevance)
        if rank:
            ordered = sorted(ordered, key=lambda item: (rank.get(item["memory_id"], 1000), relevance(item)))
        cards = []
        for item in ordered[:requested_limit]:
            description = " ".join(str(item["content"]).split())
            temporal = (
                {"event_time": item["event_time"]}
                if item.get("event_time")
                else {"updated_at": item["created_at"]}
            )
            cards.append(
                {
                    "memory_id": item["memory_id"],
                    "type": item["category"],
                    "title": item["category"].replace("_", " ").title(),
                    "short_description": description[:157] + ("..." if len(description) > 157 else ""),
                    "provenance": str(item.get("source") or "approved memory")[:80],
                    **temporal,
                }
            )
        return cards[:requested_limit]

    @staticmethod
    def _relevant_recent_event(item: dict[str, Any], query_tokens: set[str]) -> bool:
        event_tokens = _tokens(str(item.get("content") or ""))
        if query_tokens & event_tokens:
            return True
        raw_time = item.get("event_time")
        if not raw_time:
            return False
        try:
            event_time = datetime.fromisoformat(str(raw_time))
            if event_time.tzinfo is None:
                event_time = event_time.replace(tzinfo=timezone.utc)
        except ValueError:
            return False
        career_terms = {"job", "offer", "interview", "application", "career", "internship"}
        return event_time >= datetime.now(timezone.utc) - timedelta(days=90) and bool(event_tokens & career_terms)

    def get_memory_details(
        self, *, user_id: str, memory_ids: list[str]
    ) -> list[dict[str, Any]]:
        unique_ids = list(dict.fromkeys(memory_ids))
        if len(unique_ids) > 3:
            raise ValueError("At most three memory IDs may be loaded at once.")
        all_memories = [
            *self.repository.list_semantic_memories(user_id),
            *self.repository.list_career_events(user_id),
            *self.repository.list_memories(user_id),
        ]
        current = {item["memory_id"]: item for item in all_memories}
        details = []
        for memory_id in unique_ids:
            item = current.get(memory_id)
            if item is None:
                raise ValueError("An approved active memory was not found for this user.")
            details.append(
                {
                    "memory_id": item["memory_id"],
                    "type": item["category"],
                    "content": item["content"],
                    "source": item["source"],
                    "event_time": item.get("event_time"),
                    "created_at": item["created_at"],
                    "supersedes_memory_id": item.get("supersedes_memory_id"),
                    "revoked_at": item.get("revoked_at"),
                }
            )
        return details

    def get_memory_source_context(
        self, *, user_id: str, memory_id: str, max_ranges: int = 2
    ) -> list[dict[str, Any]]:
        if max_ranges < 1 or max_ranges > 2:
            raise ValueError("Memory source context is limited to two ranges.")
        details = self.get_memory_details(user_id=user_id, memory_ids=[memory_id])[0]
        memory = next(
            item for item in [
                *self.repository.list_semantic_memories(user_id),
                *self.repository.list_career_events(user_id),
                *self.repository.list_memories(user_id),
            ]
            if item["memory_id"] == details["memory_id"]
        )
        conversation_id = memory.get("source_conversation_id")
        source_ids = list(memory.get("source_message_ids") or [])
        if not conversation_id or not source_ids:
            return []
        messages = self.repository.get_conversation(user_id, conversation_id)["messages"]
        positions = [index for index, item in enumerate(messages) if item["message_id"] in source_ids]
        ranges = []
        used: set[str] = set()
        for position in positions:
            selected = [
                item for item in messages[max(0, position - 2): position + 3]
                if item["message_id"] not in used
            ]
            if not selected:
                continue
            used.update(item["message_id"] for item in selected)
            ranges.append(
                {
                    "conversation_id": conversation_id,
                    "messages": [
                        {"message_id": item["message_id"], "role": item["role"], "content": item["content"]}
                        for item in selected
                    ],
                }
            )
            if len(ranges) >= max_ranges:
                break
        return ranges

    def build_context(
        self,
        *,
        user_id: str,
        conversation_id: str | None,
        intent: Any,
        query: str,
        include_source_context: bool = False,
    ) -> dict[str, Any]:
        profile, profile_references, overlay = self.profile_projection(
            user_id=user_id,
            conversation_id=conversation_id,
            intent=intent,
            query=query,
        )
        catalog = self.memory_catalog(
            user_id=user_id,
            query=query,
            intent=intent,
            current_thread_memories=overlay.get("current_thread_memories"),
        )
        selected_cards = self._select_cards(catalog, query, intent)
        details = self.get_memory_details(
            user_id=user_id,
            memory_ids=[item["memory_id"] for item in selected_cards],
        )
        source_context = []
        if include_source_context:
            for item in details[:2]:
                source_context.extend(
                    self.get_memory_source_context(
                        user_id=user_id, memory_id=item["memory_id"], max_ranges=1
                    )
                )
        references = {
            "profile": profile_references,
            "approved_memories": [
                {
                    "memory_id": item["memory_id"],
                    "title": item["type"].replace("_", " ").title(),
                    "summary": item["content"][:160],
                }
                for item in details
            ],
        }
        return {
            "profile": profile,
            "memory_catalog": catalog,
            "memory_details": details,
            "memory_source_context": source_context[:2],
            "current_conversation_overlay": overlay,
            "personalization_references": references,
        }

    @staticmethod
    def _select_cards(
        cards: list[dict[str, Any]], query: str, intent: Any
    ) -> list[dict[str, Any]]:
        query_tokens = _tokens(query)
        intent_value = _intent_value(intent)
        workflow_terms = {
            "job_search": {"job", "jobs", "role", "roles", "internship", "internships", "work"},
            "people_search": {"people", "alumni", "mentor", "network", "professor"},
            "resume_revision": {"resume", "cv", "tailor", "revise"},
            "outreach": {"outreach", "message", "email", "follow", "contact"},
            "action_plan": {"plan", "career", "goal", "next"},
            "concise_guidance": {"role", "roles", "career", "fit", "job", "internship"},
        }.get(intent_value, set())
        workflow_is_explicit = bool(query_tokens & workflow_terms)
        overlapping = [
            card for card in cards
            if query_tokens & _tokens(
                f"{card['type']} {card['title']} {card['short_description']}"
            )
        ]
        if overlapping:
            return overlapping[:3]
        if workflow_is_explicit:
            return cards[:3]
        selected = []
        for card in cards:
            if workflow_is_explicit and card["type"] in {
                "preference", "goal", "constraint"
            }:
                selected.append(card)
            if len(selected) == 3:
                break
        return selected


progressive_memory_service = ProgressiveMemoryService()
