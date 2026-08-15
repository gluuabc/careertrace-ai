import sys
from pathlib import Path

# Add project root to Python path
ROOT = Path(__file__).resolve().parents[2]

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


import tempfile
import os
from typing import Any

import streamlit as st

from langgraph.types import Command

from app.auth import require_authenticated_user
from app.database import init_db, profile_repository
from app.graph.profile_graph import profile_graph
from app.nodes.validation import find_profile_issues
from app.services import document_service, respond_to_user
from app.services.outreach import outreach_service
from app.services.people_search import validate_connection_csv
from app.services.profile_mutation import profile_mutation_service
from app.services.conversation_memory import trigger_conversation_boundary


TOP_LEVEL_PAGE_LABELS = (
    "Documents",
    "My profile",
    "Starred Q&A",
    "Memory",
    "Career Assistant",
)
DOCUMENT_PAGE_SECTIONS = ("Upload & Analyze", "Stored Documents")


def _comma_list(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _line_list(value: str) -> list[str]:
    return [item.strip() for item in value.splitlines() if item.strip()]


def _projects_from_text(value: str) -> list[dict[str, str]]:
    projects: list[dict[str, str]] = []
    for line in _line_list(value):
        title, separator, description = line.partition("|")
        projects.append(
            {
                "title": title.strip(),
                "description": description.strip() if separator else "",
            }
        )
    return projects


def _experience_from_text(value: str) -> list[dict[str, str]]:
    experience: list[dict[str, str]] = []
    for line in _line_list(value):
        parts = [part.strip() for part in line.split("|", 2)]
        parts.extend([""] * (3 - len(parts)))
        experience.append(
            {
                "organization": parts[0],
                "role": parts[1],
                "description": parts[2],
            }
        )
    return experience


def _projects_to_text(projects: list[Any]) -> str:
    lines: list[str] = []
    for project in projects:
        if isinstance(project, dict):
            lines.append(
                f"{project.get('title', '')} | {project.get('description', '')}".strip()
            )
        else:
            lines.append(str(project))
    return "\n".join(lines)


def _experience_to_text(experience: list[Any]) -> str:
    lines: list[str] = []
    for item in experience:
        if isinstance(item, dict):
            lines.append(
                " | ".join(
                    [
                        str(item.get("organization", "")),
                        str(item.get("role", "")),
                        str(item.get("description", "")),
                    ]
                ).strip()
            )
        else:
            lines.append(str(item))
    return "\n".join(lines)


def _interrupt_value(result: dict[str, Any]) -> dict[str, Any] | None:
    interrupts = result.get("__interrupt__") or ()
    if not interrupts:
        return None
    interrupt_item = interrupts[0]
    value = getattr(interrupt_item, "value", interrupt_item)
    return value if isinstance(value, dict) else None


def _profile_form(
    profile: dict[str, Any],
    key_prefix: str,
    show_required: bool = False,
    identity_locked: bool = False,
    collapse_preferences: bool = False,
) -> dict[str, Any]:
    required = " *" if show_required else ""
    left, right = st.columns(2)
    with left:
        name = st.text_input(
            "Account name" if identity_locked else "Name (optional)",
            value=profile.get("name") or "",
            key=f"{key_prefix}_name",
            disabled=identity_locked,
        )
        school = st.text_input(
            f"School{required}",
            value=profile.get("school") or "",
            key=f"{key_prefix}_school",
        )
        if show_required and not school.strip():
            st.caption(":red[School is required.]")
        major = st.text_input(
            f"Major{required}",
            value=profile.get("major") or "",
            key=f"{key_prefix}_major",
        )
        if show_required and not major.strip():
            st.caption(":red[Major is required.]")
        graduation_year = st.number_input(
            f"Graduation year{required}",
            min_value=1950,
            max_value=2100,
            value=int(profile.get("graduation_year") or 2030),
            step=1,
            key=f"{key_prefix}_graduation_year",
        )
    with right:
        email = st.text_input(
            "Account email" if identity_locked else "Email (optional)",
            value=profile.get("email") or "",
            key=f"{key_prefix}_email",
            disabled=identity_locked,
        )
        career_goal = st.text_area(
            "Career goal (optional)",
            value=profile.get("career_goal") or "",
            key=f"{key_prefix}_career_goal",
        )

    skills = st.text_input(
        f"Skills{required} (comma-separated)",
        value=", ".join(profile.get("skills") or []),
        key=f"{key_prefix}_skills",
    )
    if show_required and not _comma_list(skills):
        st.caption(":red[At least one skill is required.]")
    courses = st.text_input(
        "Courses (optional, comma-separated)",
        value=", ".join(profile.get("courses") or []),
        key=f"{key_prefix}_courses",
    )
    certifications = st.text_input(
        "Certifications (optional, comma-separated)",
        value=", ".join(profile.get("certifications") or []),
        key=f"{key_prefix}_certifications",
    )
    achievements = st.text_area(
        "Achievements (optional, one per line)",
        value="\n".join(profile.get("achievements") or []),
        key=f"{key_prefix}_achievements",
    )
    projects = st.text_area(
        "Projects (optional) — one per line: title | description",
        value=_projects_to_text(profile.get("projects") or []),
        key=f"{key_prefix}_projects",
    )
    experience = st.text_area(
        (
            f"Experience{required} — one per line: "
            "organization | role | description"
        ),
        value=_experience_to_text(profile.get("experience") or []),
        key=f"{key_prefix}_experience",
    )
    if show_required and not _experience_from_text(experience):
        st.caption(":red[At least one experience entry is required.]")

    def render_preferences() -> tuple[str, str, str, str, str]:
        target = st.text_input(
            "Target roles (optional, comma-separated)",
            value=", ".join(profile.get("target_roles") or []),
            key=f"{key_prefix}_target_roles",
        )
        locations = st.text_input(
            "Preferred locations (optional, comma-separated)",
            value=", ".join(profile.get("preferred_locations") or []),
            key=f"{key_prefix}_locations",
        )
        employment = st.text_input(
            "Employment types (optional, comma-separated)",
            value=", ".join(profile.get("employment_types") or []),
            key=f"{key_prefix}_employment",
        )
        authorization = st.text_input(
            "Work authorization (optional)",
            value=profile.get("work_authorization") or "",
            key=f"{key_prefix}_authorization",
        )
        remote_options = ["", "Remote", "Hybrid", "On-site", "Flexible"]
        current_remote = profile.get("remote_preference") or ""
        remote = st.selectbox(
            "Remote preference (optional)",
            remote_options,
            index=(
                remote_options.index(current_remote)
                if current_remote in remote_options
                else 0
            ),
            key=f"{key_prefix}_remote",
        )
        return target, locations, employment, authorization, remote

    if collapse_preferences:
        with st.expander("Career preferences", expanded=False):
            (
                target_roles,
                preferred_locations,
                employment_types,
                work_authorization,
                remote_preference,
            ) = render_preferences()
    else:
        st.caption("Career preferences")
        (
            target_roles,
            preferred_locations,
            employment_types,
            work_authorization,
            remote_preference,
        ) = render_preferences()

    return {
        "name": name.strip() or None,
        "email": email.strip() or None,
        "education": list(profile.get("education") or []),
        "school": school.strip() or None,
        "major": major.strip() or None,
        "graduation_year": int(graduation_year),
        "career_goal": career_goal.strip() or None,
        "skills": _comma_list(skills),
        "courses": _comma_list(courses),
        "achievements": _line_list(achievements),
        "certifications": _comma_list(certifications),
        "projects": _projects_from_text(projects),
        "experience": _experience_from_text(experience),
        "target_roles": _comma_list(target_roles),
        "preferred_locations": _comma_list(preferred_locations),
        "employment_types": _comma_list(employment_types),
        "work_authorization": work_authorization.strip() or None,
        "remote_preference": remote_preference or None,
    }


def _resume_graph(command: Any, thread_id: str) -> dict[str, Any]:
    with st.spinner("CareerTrace is processing your profile…"):
        result = profile_graph.invoke(
            command,
            config={"configurable": {"thread_id": thread_id}},
        )
    st.session_state.workflow_result = result
    return result


def _workflow_thread_id(user_id: str) -> str:
    return f"profile-onboarding:{user_id}"


def _restore_pending_workflow(user_id: str) -> None:
    """Reconnect Streamlit to a durable interrupted LangGraph workflow."""

    if st.session_state.get("workflow_result"):
        return
    thread_id = _workflow_thread_id(user_id)
    snapshot = profile_graph.get_state(
        {"configurable": {"thread_id": thread_id}}
    )
    if not snapshot.interrupts:
        return
    result = dict(snapshot.values)
    result["__interrupt__"] = snapshot.interrupts
    st.session_state.workflow_thread_id = thread_id
    st.session_state.workflow_result = result


def _render_workflow(user_id: str) -> None:
    result = st.session_state.get("workflow_result")
    thread_id = st.session_state.get("workflow_thread_id")
    if not result or not thread_id:
        return

    pending = _interrupt_value(result)
    if pending and pending.get("type") == "missing_profile_fields":
        missing = pending.get("missing_fields") or []
        st.warning("Please provide the missing required profile information.")
        for error in pending.get("validation_errors") or []:
            st.error(error)

        with st.form(f"missing_fields_{thread_id}"):
            updates: dict[str, Any] = {}
            if "school" in missing:
                updates["school"] = st.text_input("School *")
            if "major" in missing:
                updates["major"] = st.text_input("Major *")
            if "graduation_year" in missing:
                updates["graduation_year"] = st.number_input(
                    "Graduation year *",
                    min_value=1950,
                    max_value=2100,
                    value=None,
                )
            if "skills" in missing:
                skills = st.text_input("Skills * (comma-separated)")
                updates["skills"] = _comma_list(skills)
            if "experience" in missing:
                experience = st.text_area(
                    "Experience * — one per line: "
                    "organization | role | description"
                )
                updates["experience"] = _experience_from_text(experience)

            if st.form_submit_button("Continue"):
                _resume_graph(Command(resume=updates), thread_id)
                st.rerun()

    elif pending and pending.get("type") == "confirm_profile":
        st.subheader("Review your profile")
        st.caption(
            "Fields marked with * are required. Skills and experience remain "
            "required according to the existing onboarding rules."
        )
        account = profile_repository.get_user(user_id)
        review_profile = dict(pending["profile"])
        if account.get("google_id"):
            review_profile.update(name=account["name"], email=account["email"])
        profile = _profile_form(
            review_profile,
            f"confirm_{thread_id}",
            show_required=True,
            identity_locked=bool(account.get("google_id")),
        )
        missing_fields, errors = find_profile_issues(profile)
        if missing_fields:
            st.warning(
                "Complete the required fields: "
                + ", ".join(field.replace("_", " ") for field in missing_fields)
            )
        for error in errors:
            st.error(error)

        with st.container(horizontal=True, horizontal_alignment="right"):
            cancelled = st.button(
                "Cancel",
                key=f"cancel_{thread_id}",
                icon=":material/close:",
            )
            confirmed = st.button(
                "Confirm and save",
                type="primary",
                key=f"confirm_{thread_id}",
                icon=":material/check:",
                disabled=bool(missing_fields or errors),
            )

        if confirmed or cancelled:
            response = {
                "confirmed": confirmed,
                "profile": profile,
            }
            _resume_graph(Command(resume=response), thread_id)
            st.rerun()

    elif result.get("confirmed"):
        if result.get("saved_profile", {}).get("profile_changed") is False:
            st.info(
                "No profile changes detected. The profile version and existing "
                "career analysis were not updated."
            )
        else:
            st.success("Profile and career analysis were saved to SQL memory.")
            st.json(result.get("career_profile") or {})
    else:
        st.info("Onboarding was cancelled. No profile changes were saved.")


def _render_upload(user_id: str, *, is_demo: bool = False) -> None:
    st.subheader("Career document onboarding")
    _restore_pending_workflow(user_id)
    if is_demo:
        st.info(
            "Download the synthetic files below, then upload them together to "
            "exercise the same S3, extraction, confirmation, and SQL workflow "
            "used by Google-authenticated users."
        )
        with st.container(horizontal=True):
            for filename, label in (
                ("Demo_Resume.pdf", "Download demo resume"),
                ("Demo_Portfolio.pdf", "Download demo portfolio"),
            ):
                path = ROOT / "demo" / filename
                st.download_button(
                    label,
                    data=path.read_bytes(),
                    file_name=filename,
                    mime="application/pdf",
                    key=f"download_{filename}",
                    on_click="ignore",
                )
    max_size_mib = min(int(os.getenv("MAX_DOCUMENT_SIZE_MIB", "10")), 10)
    uploaded_files = st.file_uploader(
        "Upload one or more PDF or DOCX career documents",
        type=["pdf", "docx"],
        accept_multiple_files=True,
        max_upload_size=max_size_mib,
        help=(
            f"Original documents are stored privately in S3. "
            f"Maximum size: {max_size_mib} MiB."
        ),
    )
    document_types: list[str] = []
    for index, uploaded_file in enumerate(uploaded_files or []):
        document_types.append(
            st.selectbox(
                f"Type for {uploaded_file.name}",
                ["resume", "portfolio", "transcript", "certificate", "other"],
                index=0 if index == 0 else 1,
                key=f"onboarding_type_{index}_{uploaded_file.name}",
            )
        )

    if st.button(
        "Analyze documents",
        type="primary",
        icon=":material/description:",
        disabled=not uploaded_files,
    ):
        thread_id = _workflow_thread_id(user_id)
        profile_graph.checkpointer.delete_thread(thread_id)
        pending_documents: list[dict[str, Any]] = []
        temporary_paths: list[Path] = []
        for index, uploaded_file in enumerate(uploaded_files):
            suffix = Path(uploaded_file.name).suffix.lower()
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as stream:
                stream.write(uploaded_file.getvalue())
                path = Path(stream.name)
            temporary_paths.append(path)
            pending_documents.append(
                {
                    "path": str(path),
                    "original_filename": uploaded_file.name,
                    "content_type": uploaded_file.type,
                    "document_type": document_types[index],
                }
            )
        try:
            st.session_state.workflow_thread_id = thread_id
            try:
                _resume_graph(
                    {
                        "documents": pending_documents,
                        "existing_profile": profile_repository.get_profile(user_id),
                        "user_id": user_id,
                        "confirmed": False,
                    },
                    thread_id,
                )
            except Exception as error:
                st.error(f"Resume processing failed: {error}")
                return
        finally:
            for path in temporary_paths:
                path.unlink(missing_ok=True)
        st.rerun()

    _render_workflow(user_id)


def _render_profile(user_id: str) -> None:
    profile = profile_repository.get_profile(user_id)
    if profile is None:
        st.info("Upload a resume to create your stored profile.")
        return

    st.subheader("My profile")
    st.caption("Current values are stored in SQL; history is tracked per field.")
    st.markdown("### Current Profile")
    if profile.get("source_documents"):
        st.caption(
            "Sources: "
            + ", ".join(
                item["filename"] for item in profile["source_documents"]
            )
        )
    restore_preview = st.session_state.get("profile_restore_preview") or {}
    form_profile = {**profile, **restore_preview}
    with st.form("edit_profile"):
        account = profile_repository.get_user(user_id)
        updated_profile = _profile_form(
            form_profile,
            "edit",
            show_required=True,
            identity_locked=bool(account.get("google_id")),
            collapse_preferences=True,
        )
        if st.form_submit_button("Save profile changes", type="primary"):
            missing_fields, errors = find_profile_issues(updated_profile)
            if missing_fields or errors:
                details = ", ".join(missing_fields + errors)
                st.error(f"Please correct the required profile fields: {details}")
            else:
                result = profile_mutation_service.apply_profile_field_changes(
                    user_id,
                    updated_profile,
                    source_type=(
                        "history_restore" if restore_preview else "manual"
                    ),
                )
                if result["profile_changed"]:
                    st.session_state.pop("profile_restore_preview", None)
                    st.success("Profile changes saved.")
                    if result.get("retrieval_index_status") in {"failed", "sparse_only"}:
                        st.warning(
                            "The current profile is saved, but dense retrieval indexing "
                            "needs attention. Sparse/structured profile data remains current."
                        )
                    preference_labels = {
                        "target_roles": "Target roles",
                        "preferred_locations": "Preferred locations",
                        "employment_types": "Employment types",
                        "remote_preference": "Remote preference",
                        "work_authorization": "Work authorization",
                    }
                    changes = []
                    for field, label in preference_labels.items():
                        before = profile.get(field)
                        after = updated_profile.get(field)
                        if before != after:
                            changes.append(f"{label}: {before or 'none'} → {after or 'none'}")
                    if changes:
                        st.success("Preference updated:\n\n" + "\n\n".join(changes))
                else:
                    st.info("No changes detected. Profile version was not updated.")

    st.markdown("### Pending Profile Updates")
    profile_drafts = profile_repository.list_profile_revision_drafts(user_id)
    pending_profile_drafts = [
        item for item in profile_drafts if item["status"] == "pending"
    ]
    if not pending_profile_drafts:
        st.info("No conversation-derived profile changes are waiting for review.")
    for draft in pending_profile_drafts:
        with st.container(border=True):
            st.caption("Profile change proposal — reviewed one field at a time")
            for change in draft["changes"]:
                st.write(f"**{change['field_key']}** · {change['operation']}")
                st.write(f"Current: {change['before_value']}")
                st.write(f"Proposed: {change['proposed_value']}")
                if change["status"] == "pending":
                    with st.container(horizontal=True):
                        if st.button("Accept field change", key=f"accept_profile_change_{change['change_id']}"):
                            profile_repository.review_profile_revision_change(user_id, change["change_id"], accept=True)
                            st.rerun()
                        if st.button("Reject field change", key=f"reject_profile_change_{change['change_id']}"):
                            profile_repository.review_profile_revision_change(user_id, change["change_id"], accept=False)
                            st.rerun()
                else:
                    st.caption(f"Status: {change['status']}")
            if any(change["status"] == "accepted" for change in draft["changes"]):
                if st.button("Apply accepted changes", key=f"apply_profile_draft_{draft['draft_id']}"):
                    try:
                        profile_repository.apply_profile_revision_draft(user_id, draft["draft_id"])
                        st.success("Accepted profile fields were applied.")
                        st.rerun()
                    except ValueError as error:
                        st.error(str(error))

    st.markdown("### Field History")
    history = profile_repository.list_profile_field_history(user_id)
    with st.expander("Previous values"):
        has_history = False
        for field_key, entries in history.items():
            if not entries:
                continue
            has_history = True
            st.markdown(f"**{field_key.replace('_', ' ').title()}**")
            for index, entry in enumerate(entries):
                value = entry["value"]
                st.code(str(value), language=None)
                if st.button(
                    "Use this value",
                    key=f"restore_{field_key}_{index}",
                ):
                    st.session_state.profile_restore_preview = {field_key: value}
                    for key in list(st.session_state):
                        if key.startswith("edit_"):
                            del st.session_state[key]
                    st.rerun()
        if not has_history:
            st.info("No field has changed yet.")


def _render_starred_qa(user_id: str) -> None:
    st.subheader("Starred Q&A")
    pairs = profile_repository.list_starred_qa_pairs(user_id)
    if not pairs:
        st.info(
            "No starred Q&A yet. Star useful CareerTrace responses from a "
            "conversation to save them here."
        )
        return
    for pair in pairs:
        with st.container(border=True):
            st.markdown(f"### ★ {pair['question']}")
            st.write(pair["answer"])
            st.caption(
                f"Conversation: {pair['conversation_title']} · Saved: "
                f"{pair['created_at']}"
            )
            if pair.get("preference_update_summary"):
                st.info(f"Preference updated: {pair['preference_update_summary']}")
            if st.button(
                "Unstar",
                key=f"unstar_page_{pair['starred_qa_id']}",
                icon=":material/star:",
            ):
                profile_repository.unstar_qa_pair(
                    user_id, pair["starred_qa_id"]
                )
                st.rerun()


def _render_documents(user_id: str) -> None:
    st.subheader("Documents")
    st.caption(
        "Documents are private S3 objects. SQLite stores only their metadata."
    )
    max_size_mib = min(int(os.getenv("MAX_DOCUMENT_SIZE_MIB", "10")), 10)

    with st.form("document_upload"):
        uploaded_documents = st.file_uploader(
            "Upload PDF or DOCX documents",
            type=["pdf", "docx"],
            accept_multiple_files=True,
            max_upload_size=max_size_mib,
            key="document_file",
        )
        document_type = st.selectbox(
            "Document type for this batch",
            ["resume", "portfolio", "transcript", "certificate", "other"],
            index=1,
            key="document_type",
        )
        submitted = st.form_submit_button(
            "Store document",
            type="primary",
            icon=":material/cloud_upload:",
        )

    if submitted:
        if not uploaded_documents:
            st.warning("Choose at least one PDF or DOCX document first.")
        else:
            try:
                for uploaded in uploaded_documents:
                    document_service.upload(
                        user_id=user_id,
                        filename=uploaded.name,
                        content_type=uploaded.type,
                        data=uploaded.getvalue(),
                        document_type=document_type or "other",
                    )
                st.success(f"Stored {len(uploaded_documents)} document(s) privately.")
                st.rerun()
            except Exception as error:
                st.error(f"Document upload failed: {error}")

    documents = profile_repository.list_documents(user_id)
    if not documents:
        st.info("No stored documents yet.")
        return

    for document in documents:
        with st.container(border=True):
            st.markdown(f"**{document['filename']}**")
            st.caption(
                f"{document['document_type']} · "
                f"{document['size_bytes'] / 1024:.1f} KiB · "
                f"uploaded {document['uploaded_at']}"
            )
            related_versions = document.get("profile_versions") or []
            st.caption(
                "Related profile versions: "
                + (
                    ", ".join(f"v{number}" for number in related_versions)
                    if related_versions
                    else "None"
                )
            )
            with st.container(horizontal=True):
                st.download_button(
                    "Download",
                    data=lambda document_id=document["document_id"]: (
                        document_service.download(user_id, document_id)
                    ),
                    file_name=document["filename"],
                    mime=document["content_type"],
                    key=f"download_{document['document_id']}",
                    icon=":material/download:",
                    on_click="ignore",
                )
                if st.button(
                    "Delete",
                    key=f"delete_{document['document_id']}",
                    icon=":material/delete:",
                ):
                    try:
                        document_service.delete(user_id, document["document_id"])
                        st.success("Document deleted.")
                        st.rerun()
                    except Exception as error:
                        st.error(f"Document deletion failed: {error}")


def _render_documents_page(user_id: str, *, is_demo: bool) -> None:
    """Compose the existing upload workflow and stored-document UI in one page."""

    upload_tab, stored_tab = st.tabs(DOCUMENT_PAGE_SECTIONS)
    with upload_tab:
        _render_upload(user_id, is_demo=is_demo)
    with stored_tab:
        _render_documents(user_id)


def _render_memory(user_id: str) -> None:
    st.subheader("Memory")
    st.caption(
        "Structured career facts are managed in My Profile. Review flexible "
        "long-term context here."
    )

    st.markdown("### Memory Candidates")
    candidates = profile_repository.list_memory_candidates(user_id)
    pending = [item for item in candidates if item["status"] == "pending"]
    if not pending:
        st.info("No AI memory suggestions are waiting for review.")
    for candidate in pending:
        with st.container(border=True):
            st.write(f"**{candidate['operation']} {candidate['category']}** — {candidate['content']}")
            existing = next(
                (
                    item for item in profile_repository.list_memories(user_id, include_inactive=True)
                    if item["memory_id"] == candidate.get("existing_memory_id")
                ),
                None,
            )
            if existing:
                st.write(f"Existing: {existing['content']}")
            confidence = (
                candidate["confidence"]
                if candidate["confidence"] is not None
                else "n/a"
            )
            st.caption(
                f"Source: {candidate['source']} · confidence: "
                f"{confidence}"
            )
            with st.container(horizontal=True):
                accept_label = {
                    "ADD": "Approve",
                    "UPDATE": "Approve update",
                    "REVOKE": "Approve revoke",
                    "CONFLICT": "Use new",
                }.get(candidate["operation"], "Approve")
                if st.button(
                    accept_label,
                    key=f"accept_memory_{candidate['candidate_id']}",
                ):
                    result = profile_repository.review_memory_candidate(
                        user_id, candidate["candidate_id"], accept=True
                    )
                    if result and result.get("retrieval_index_status") == "failed":
                        st.warning("Saved in SQL, but assistant retrieval indexing needs retry.")
                    st.rerun()
                if st.button(
                    "Reject",
                    key=f"reject_memory_{candidate['candidate_id']}",
                ):
                    profile_repository.review_memory_candidate(
                        user_id, candidate["candidate_id"], accept=False
                    )
                    st.rerun()
                if candidate["operation"] == "CONFLICT" and st.button(
                    "Keep existing",
                    key=f"keep_existing_memory_{candidate['candidate_id']}",
                ):
                    profile_repository.review_memory_candidate(
                        user_id, candidate["candidate_id"], accept=False,
                        conflict_resolution="keep_existing",
                    )
                    st.rerun()
                if candidate["operation"] == "CONFLICT" and st.button(
                    "Keep both",
                    key=f"keep_both_memory_{candidate['candidate_id']}",
                ):
                    profile_repository.review_memory_candidate(
                        user_id, candidate["candidate_id"], accept=True,
                        conflict_resolution="keep_both",
                    )
                    st.rerun()

    st.markdown("### Approved Memories")
    memories = profile_repository.list_memories(user_id)
    if not memories:
        st.info("No approved flexible memories yet.")
    for memory in memories:
        with st.container(border=True):
            st.write(memory["content"])
            timestamp = memory.get("event_time") or memory["created_at"]
            st.caption(
                f"Type: {memory['category']} · Source: {memory['source']} · "
                f"Date: {timestamp}"
            )
            if memory["retrieval_index_status"] == "failed":
                st.warning("Saved in SQL; assistant retrieval indexing needs retry.")


def _render_connections(user_id: str) -> None:
    with st.expander("People Search connections"):
        st.caption(
            "Optional private inputs. Public-source evidence is still required for "
            "external identity claims."
        )
        uploaded = st.file_uploader(
            "Import connections CSV",
            type=["csv"],
            key="connection_csv",
            help="Required column: name. Maximum 500 rows.",
        )
        if uploaded and st.button("Validate and import CSV", key="import_connections"):
            rows, errors = validate_connection_csv(
                uploaded.getvalue().decode("utf-8-sig", errors="replace")
            )
            for error in errors:
                st.error(error)
            if not errors:
                for row in rows:
                    profile_repository.create_connection(user_id, row)
                st.success(f"Imported {len(rows)} connections.")
                st.rerun()
        with st.form("manual_connection"):
            name = st.text_input("Name *")
            role = st.text_input("Current role")
            organization = st.text_input("Organization")
            public_url = st.text_input("Public professional/profile URL")
            email = st.text_input("User-provided email (private)")
            notes = st.text_area("Private notes")
            if st.form_submit_button("Add connection"):
                try:
                    profile_repository.create_connection(
                        user_id,
                        {
                            "name": name,
                            "current_role": role,
                            "organization": organization,
                            "public_profile_url": public_url,
                            "user_provided_email": email,
                            "notes": notes,
                            "source_type": "manual",
                        },
                    )
                    st.success("Connection saved.")
                    st.rerun()
                except ValueError as error:
                    st.error(str(error))
        connections = profile_repository.list_connections(user_id)
        if connections:
            st.caption(f"{len(connections)} private connection records available.")


def _render_agent_activity(
    user_id: str, conversation_id: str | None
) -> None:
    with st.sidebar:
        st.subheader("CareerTrace Status")
        if not conversation_id:
            st.caption("No activity yet for this conversation.")
            return
        runs = profile_repository.list_agent_runs(user_id, conversation_id)
        if not runs:
            st.caption("No activity yet for this conversation.")
            return
        latest = runs[0]
        state = latest.get("state") or {}
        status = state.get("status") or {}
        st.write("**Goal**")
        st.write(latest["goal"] or "Not set")
        st.caption(
            f"{state.get('workflow_stage') or latest['status']} · "
            f"{latest['intent'] or 'classifying'}"
        )
        markers = {
            "completed": "✓",
            "in_progress": "●",
            "pending": "○",
            "blocked": "⚠",
            "cancelled": "—",
        }
        for item in state.get("todo_items") or []:
            st.write(
                f"{markers.get(item.get('status'), '○')} {item.get('content', '')}"
            )
        candidate_count = int(state.get("candidate_count") or 0)
        if candidate_count:
            st.caption(
                f"{state.get('verified_candidate_count', 0)} verified · "
                f"{state.get('unverified_candidate_count', 0)} unverified"
            )
        source_count = int(state.get("source_call_count") or 0)
        if source_count:
            st.caption(f"{source_count} source calls")
        for warning in (state.get("warnings") or [])[:3]:
            lowered = str(warning).casefold()
            if "budget" in lowered:
                message = "The configured workflow budget was reached."
            elif "routing" in lowered or "classifier" in lowered:
                message = "Structured routing fallback was used."
            else:
                message = "A provider or source was unavailable; available results were preserved."
            st.warning(message)
        with st.expander("Activity details", expanded=False):
            for step in latest["steps"]:
                summary = (
                    "Structured routing fallback was used."
                    if step["stage"] == "routing_warning"
                    else step["display_summary"]
                )
                st.caption(f"{step['stage']} · {step['status']} · {summary}")
            for call in latest["tool_calls"]:
                duration = (
                    f"{call['duration_ms']} ms"
                    if call["duration_ms"] is not None
                    else "duration unknown"
                )
                st.caption(
                    f"Tool: {call['tool_name']} · {call['status']} · {duration}"
                )


def _render_agent_results(user_id: str, conversation_id: str) -> None:
    result = st.session_state.get("agent_last_result") or {}
    if result.get("conversation_id") == conversation_id:
        references = result.get("personalization_references") or {}
        profile_references = references.get("profile") or []
        memory_references = references.get("approved_memories") or []
        if profile_references or memory_references:
            with st.expander("Personalization references", expanded=False):
                if profile_references:
                    st.markdown("**Profile**")
                    for item in profile_references:
                        st.write(f"- {item['field']}: {item['value']}")
                if memory_references:
                    st.markdown("**Approved Memories**")
                    for item in memory_references:
                        st.write(f"- {item['title']}: {item['summary']}")
        jobs = result.get("job_candidates") or []
        demo_jobs = [item for item in jobs if item.get("is_demo_sample")]
        live_jobs = [item for item in jobs if not item.get("is_demo_sample")]
        verified = [item for item in live_jobs if item.get("hard_constraints_met")]
        unverified = [item for item in live_jobs if not item.get("hard_constraints_met")]
        if verified:
            st.markdown("### Live results — matching requirements")
            for item in verified:
                with st.container(border=True):
                    st.write(f"**{item.get('title') or 'unknown'} — {item.get('company') or 'unknown'}**")
                    st.write(f"Location: {item.get('location') or 'unknown'} · Employment: {item.get('employment_type') or 'unknown'}")
                    st.write(f"Eligibility: {item.get('eligibility') or 'unknown'}")
                    if item.get("application_url"):
                        st.link_button("Official application", item["application_url"])
                    st.caption(
                        f"Source status: {str(item.get('source_status') or 'unknown').replace('_', ' ').title()} · "
                        f"Requirement status: {str(item.get('requirement_status') or 'unknown').replace('_', ' ').title()} · "
                        f"Retrieved: {item.get('retrieved_at')}"
                    )
        if unverified:
            st.markdown("### Live results — requirements not fully verified")
            st.caption(
                "These may come from an official source, but one or more requested "
                "requirements were not stated. They do not count toward the matching total."
            )
            for item in unverified:
                st.markdown(
                    f"- **{item.get('title') or 'unknown'} — {item.get('company') or 'unknown'}** · "
                    f"{str(item.get('source_status') or 'unknown').replace('_', ' ').title()} · "
                    f"[source]({item.get('source_url')})"
                )
        if demo_jobs:
            st.markdown("### Demo snapshot suggestions")
            st.warning(
                "Historical public-source samples for judge testing. These are not "
                "claimed to be currently open postings."
            )
            for item in demo_jobs:
                st.markdown(
                    f"- **{item.get('title') or 'unknown'} — {item.get('company') or 'unknown'}** · "
                    f"snapshot {item.get('snapshot_date') or 'date unknown'} · "
                    f"[snapshot source]({item.get('source_url')})"
                )
        people = result.get("people_candidates") or []
        live_people = [item for item in people if not item.get("is_demo_sample")]
        demo_people = [item for item in people if item.get("is_demo_sample")]
        if live_people:
            st.markdown("### Live people results")
            for item in live_people:
                with st.container(border=True):
                    st.write(f"**{item.get('name')}** — {item.get('current_role') or 'role unknown'}")
                    st.write(f"Organization: {item.get('organization') or 'unknown'}")
                    st.write("Relevant connection: " + ("; ".join(item.get("relevant_connection") or []) or "unknown"))
                    public_channels = [channel for channel in item.get("contact_channels") or [] if channel.get("visibility") == "public"]
                    st.write("Public contact: " + (", ".join(channel.get("value", "") for channel in public_channels) or "unavailable"))
                    st.link_button("Public source", item["public_source_url"])
        if demo_people:
            st.markdown("### Demo snapshot suggestions")
            st.warning(
                "Historical OpenAlex samples for judge testing. Current roles and "
                "affiliations are not asserted."
            )
            for item in demo_people:
                st.markdown(
                    f"- **{item.get('name') or 'unknown'}** · snapshot "
                    f"{item.get('snapshot_date') or 'date unknown'} · "
                    f"[snapshot source]({item.get('public_source_url')})"
                )

    resume_drafts = profile_repository.list_resume_revision_drafts(user_id)
    if resume_drafts:
        st.markdown("### Resume revision drafts")
        for draft in resume_drafts:
            with st.expander(f"Draft / Not applied — {draft['summary']}"):
                for change in draft["changes"]:
                    st.write(f"**{change['section']}**")
                    st.write(f"Original: {change['original_text'] or 'unknown'}")
                    st.write(f"Proposed: {change['proposed_text']}")
                    st.caption(f"Reason: {change['rationale']} · Evidence: {', '.join(change['profile_evidence_ids'] + change['job_evidence_ids']) or 'none'}")
                    for warning in change["warnings"]:
                        st.warning(warning)
    outreach_drafts = profile_repository.list_outreach_drafts(user_id)
    if outreach_drafts:
        st.markdown("### Outreach drafts")
        for draft in outreach_drafts:
            with st.expander(f"{draft['status'].title()} / {'Sent' if draft['sent_at'] else 'Not sent'} — {draft['recipient_name']}"):
                st.text_input("Subject", value=draft["subject"], disabled=True, key=f"draft_subject_{draft['draft_id']}")
                st.text_area("Body", value=draft["body"], disabled=True, key=f"draft_body_{draft['draft_id']}")
                with st.container(horizontal=True):
                    if draft["status"] == "draft" and st.button("Mark ready", key=f"ready_{draft['draft_id']}"):
                        outreach_service.mark_status(user_id, draft["draft_id"], "ready", explicit_user_action=True)
                        st.rerun()
                    if draft["status"] == "ready" and st.button("Mark sent", key=f"sent_{draft['draft_id']}"):
                        outreach_service.mark_status(user_id, draft["draft_id"], "sent", explicit_user_action=True)
                        st.rerun()
                    if draft["status"] != "archived" and st.button("Archive", key=f"archive_{draft['draft_id']}"):
                        outreach_service.mark_status(user_id, draft["draft_id"], "archived", explicit_user_action=True)
                        st.rerun()


def _render_career_assistant(user_id: str) -> None:
    st.subheader("Career Assistant")
    st.caption(
        "Conversations are stored in SQL. Chat does not automatically change "
        "your profile or memory."
    )
    _render_connections(user_id)
    conversations = profile_repository.list_conversations(user_id)
    st.markdown("### Previous Conversations")
    if st.button("New conversation", icon=":material/add:"):
        previous_id = st.session_state.get("active_conversation_id")
        if previous_id:
            try:
                trigger_conversation_boundary(
                    user_id, str(previous_id), process_now=True,
                    repository=profile_repository,
                )
            except Exception:
                st.warning("Memory extraction is pending and will retry after login.")
        conversation = profile_repository.create_conversation(
            user_id, f"Career conversation {len(conversations) + 1}"
        )
        st.session_state.active_conversation_id = conversation["conversation_id"]
        st.rerun()

    if not conversations:
        _render_agent_activity(user_id, None)
        st.info("Create a conversation to start chatting.")
        return
    conversation_ids = [item["conversation_id"] for item in conversations]
    previous_active_id = st.session_state.get("active_conversation_id")
    active_id = previous_active_id
    if active_id not in conversation_ids:
        active_id = conversation_ids[0]
    active_id = st.selectbox(
        "Continue conversation",
        conversation_ids,
        index=conversation_ids.index(active_id),
        key="conversation_selector",
        format_func=lambda item_id: next(
            item["title"] for item in conversations if item["conversation_id"] == item_id
        ),
    )
    if previous_active_id and active_id != previous_active_id:
        try:
            trigger_conversation_boundary(
                user_id, str(previous_active_id), process_now=True,
                repository=profile_repository,
            )
        except Exception:
            st.warning("Memory extraction is pending and will retry after login.")
    st.session_state.active_conversation_id = active_id
    active_title = next(
        item["title"] for item in conversations
        if item["conversation_id"] == active_id
    )
    with st.expander("Rename conversation"):
        with st.form(f"rename_conversation_{active_id}"):
            renamed_title = st.text_input(
                "Conversation title",
                value=active_title,
                key=f"conversation_title_{active_id}",
            )
            if st.form_submit_button("Save title"):
                try:
                    profile_repository.rename_conversation(
                        user_id, active_id, renamed_title
                    )
                    st.success("Conversation renamed.")
                    st.rerun()
                except ValueError as error:
                    st.error(str(error))
    _render_agent_activity(user_id, active_id)
    _render_agent_results(user_id, active_id)
    conversation = profile_repository.get_conversation(user_id, active_id)
    starred = {
        item["assistant_message_id"]: item
        for item in profile_repository.list_starred_qa_pairs(user_id, active_id)
    }
    for message in conversation["messages"]:
        with st.chat_message(message["role"]):
            st.write(message["content"])
            if message["role"] == "assistant" and message.get("reply_to_message_id"):
                existing = starred.get(message["message_id"])
                label = "★ Starred" if existing else "☆ Star"
                if st.button(label, key=f"star_{message['message_id']}"):
                    if existing:
                        profile_repository.unstar_qa_pair(
                            user_id, existing["starred_qa_id"]
                        )
                    else:
                        profile_repository.star_qa_pair(
                            user_id,
                            active_id,
                            message["reply_to_message_id"],
                            message["message_id"],
                        )
                    st.rerun()

    prompt = st.chat_input("Ask CareerTrace about your career")
    if prompt:
        try:
            events: list[dict[str, Any]] = []
            with st.spinner("CareerTrace is thinking…"):
                respond_to_user(
                    user_id,
                    active_id,
                    prompt,
                    event_handler=events.append,
                )
            final_event = next(
                (item for item in reversed(events) if item.get("type") == "final"),
                None,
            )
            if final_event:
                st.session_state.agent_last_result = final_event
            st.rerun()
        except Exception as error:
            st.error(f"Career Assistant failed: {error}")


def main() -> None:
    st.set_page_config(page_title="CareerTrace AI", page_icon="🧭", layout="wide")
    init_db()

    current_user = require_authenticated_user()
    if current_user is None:
        return
    user_id = current_user["user_id"]
    is_demo = current_user.get("is_demo") is True

    st.title("CareerTrace AI")
    st.caption("Persistent career profile management with bounded AI reasoning")
    if is_demo:
        st.warning("Demo workspace — uses synthetic data")
    documents_tab, profile_tab, starred_tab, memory_tab, assistant_tab = st.tabs(
        TOP_LEVEL_PAGE_LABELS
    )
    with documents_tab:
        _render_documents_page(user_id, is_demo=is_demo)
    with profile_tab:
        _render_profile(user_id)
    with starred_tab:
        _render_starred_qa(user_id)
    with memory_tab:
        _render_memory(user_id)
    with assistant_tab:
        _render_career_assistant(user_id)


if __name__ == "__main__":
    main()
