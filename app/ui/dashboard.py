import sys
from pathlib import Path

# Add project root to Python path
ROOT = Path(__file__).resolve().parents[2]

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


import tempfile
import os
from typing import Any
from uuid import uuid4

import streamlit as st

from langgraph.types import Command

from app.auth import require_authenticated_user
from app.database import init_db, profile_repository
from app.graph.profile_graph import profile_graph
from app.nodes.profile import generate_profile
from app.nodes.validation import find_profile_issues
from app.services import document_service


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
) -> dict[str, Any]:
    required = " *" if show_required else ""
    left, right = st.columns(2)
    with left:
        name = st.text_input(
            "Name (Google account)" if identity_locked else "Name (optional)",
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
            "Email (Google account)" if identity_locked else "Email (optional)",
            value=profile.get("email") or "",
            key=f"{key_prefix}_email",
            disabled=identity_locked,
        )
        career_goal = st.text_area(
            "Career goal (optional)",
            value=profile.get("career_goal") or "",
            key=f"{key_prefix}_career_goal",
        )
        remote_preference = st.selectbox(
            "Remote preference (optional)",
            ["", "Remote", "Hybrid", "On-site", "Flexible"],
            index=(
                ["", "Remote", "Hybrid", "On-site", "Flexible"].index(
                    profile.get("remote_preference") or ""
                )
                if (profile.get("remote_preference") or "")
                in ["", "Remote", "Hybrid", "On-site", "Flexible"]
                else 0
            ),
            key=f"{key_prefix}_remote",
        )

    skills = st.text_input(
        f"Skills{required} (comma-separated)",
        value=", ".join(profile.get("skills") or []),
        key=f"{key_prefix}_skills",
    )
    if show_required and not _comma_list(skills):
        st.caption(":red[At least one skill is required.]")
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

    st.caption("Career preferences")
    target_roles = st.text_input(
        "Target roles (optional, comma-separated)",
        value=", ".join(profile.get("target_roles") or []),
        key=f"{key_prefix}_target_roles",
    )
    preferred_locations = st.text_input(
        "Preferred locations (optional, comma-separated)",
        value=", ".join(profile.get("preferred_locations") or []),
        key=f"{key_prefix}_locations",
    )
    employment_types = st.text_input(
        "Employment types (optional, comma-separated)",
        value=", ".join(profile.get("employment_types") or []),
        key=f"{key_prefix}_employment",
    )
    work_authorization = st.text_input(
        "Work authorization (optional)",
        value=profile.get("work_authorization") or "",
        key=f"{key_prefix}_authorization",
    )

    return {
        "name": name.strip() or None,
        "email": email.strip() or None,
        "education": list(profile.get("education") or []),
        "school": school.strip() or None,
        "major": major.strip() or None,
        "graduation_year": int(graduation_year),
        "career_goal": career_goal.strip() or None,
        "skills": _comma_list(skills),
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
        review_profile = {
            **pending["profile"],
            "name": account["name"],
            "email": account["email"],
        }
        profile = _profile_form(
            review_profile,
            f"confirm_{thread_id}",
            show_required=True,
            identity_locked=True,
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
    st.subheader("Resume onboarding")
    if is_demo:
        st.info(
            "Judge mode starts with a synthetic seeded profile. Resume and "
            "document uploads are disabled so the shared demo workspace never "
            "mixes judge files with real-user storage. Test profile editing and "
            "career analysis in the adjacent tabs."
        )
        return
    max_size_mib = min(int(os.getenv("MAX_DOCUMENT_SIZE_MIB", "10")), 10)
    uploaded_file = st.file_uploader(
        "Upload a PDF or DOCX resume",
        type=["pdf", "docx"],
        max_upload_size=max_size_mib,
        help=(
            f"Original documents are stored privately in S3. "
            f"Maximum size: {max_size_mib} MiB."
        ),
    )
    if st.button(
        "Analyze resume",
        type="primary",
        icon=":material/description:",
        disabled=uploaded_file is None,
    ):
        thread_id = str(uuid4())
        suffix = Path(uploaded_file.name).suffix.lower()
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as resume_file:
            resume_file.write(uploaded_file.getvalue())
            resume_path = Path(resume_file.name)
        try:
            st.session_state.workflow_thread_id = thread_id
            try:
                _resume_graph(
                    {
                        "resume_path": str(resume_path),
                        "original_filename": uploaded_file.name,
                        "content_type": uploaded_file.type,
                        "document_type": "resume",
                        "user_id": user_id,
                        "confirmed": False,
                    },
                    thread_id,
                )
            except Exception as error:
                st.error(f"Resume processing failed: {error}")
                return
        finally:
            resume_path.unlink(missing_ok=True)
        st.rerun()

    _render_workflow(user_id)


def _render_profile(user_id: str) -> None:
    profile = profile_repository.get_profile(user_id)
    if profile is None:
        st.info("Upload a resume to create your stored profile.")
        return

    st.subheader("My profile")
    with st.form("edit_profile"):
        updated_profile = _profile_form(
            profile,
            "edit",
            show_required=True,
            identity_locked=True,
        )
        if st.form_submit_button("Save profile changes", type="primary"):
            missing_fields, errors = find_profile_issues(updated_profile)
            if missing_fields or errors:
                details = ", ".join(missing_fields + errors)
                st.error(f"Please correct the required profile fields: {details}")
            else:
                result = profile_repository.upsert_profile(
                    user_id, updated_profile
                )
                if result["profile_changed"]:
                    st.success("Profile changes saved. Career analysis is now stale.")
                else:
                    st.info("No changes detected. Profile version was not updated.")


def _render_analysis(user_id: str) -> None:
    profile = profile_repository.get_profile(user_id)
    analysis = profile_repository.get_latest_analysis(user_id)
    if profile is None:
        st.info("Create a profile before generating career analysis.")
        return

    st.subheader("Career analysis")
    if analysis:
        if analysis["is_stale"]:
            st.warning(
                "Your profile has changed since this analysis was generated."
            )
        st.markdown("#### Strengths")
        for item in analysis["strengths"]:
            st.markdown(f"- {item}")
        st.markdown("#### Possible roles")
        for item in analysis["possible_roles"]:
            st.markdown(f"- {item}")
        st.markdown("#### Recommended next skills")
        for item in analysis["recommended_next_skills"]:
            st.markdown(f"- {item}")
    else:
        st.info("No stored career analysis yet.")

    if st.button("Regenerate career analysis", type="primary"):
        with st.spinner("Generating career analysis with Bedrock…"):
            generated = generate_profile({"saved_profile": profile})[
                "career_profile"
            ]
            profile_repository.save_analysis(user_id, generated)
        st.success("Career analysis updated.")
        st.rerun()


def _render_documents(user_id: str, *, is_demo: bool = False) -> None:
    st.subheader("Documents")
    if is_demo:
        st.info(
            "Document storage is disabled in the shared judge workspace. "
            "Google-authenticated accounts remain isolated in private S3 paths."
        )
        return
    st.caption(
        "Documents are private S3 objects. SQLite stores only their metadata."
    )
    max_size_mib = min(int(os.getenv("MAX_DOCUMENT_SIZE_MIB", "10")), 10)

    with st.form("document_upload"):
        uploaded = st.file_uploader(
            "Upload a PDF or DOCX document",
            type=["pdf", "docx"],
            max_upload_size=max_size_mib,
            key="document_file",
        )
        document_type = st.segmented_control(
            "Document type",
            ["resume", "portfolio"],
            default="portfolio",
            key="document_type",
        )
        submitted = st.form_submit_button(
            "Store document",
            type="primary",
            icon=":material/cloud_upload:",
        )

    if submitted:
        if uploaded is None:
            st.warning("Choose a PDF or DOCX document first.")
        else:
            try:
                document_service.upload(
                    user_id=user_id,
                    filename=uploaded.name,
                    content_type=uploaded.type,
                    data=uploaded.getvalue(),
                    document_type=document_type or "portfolio",
                )
                st.success("Document stored privately.")
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
    upload_tab, profile_tab, analysis_tab, documents_tab = st.tabs(
        ["Resume upload", "My profile", "Career analysis", "Documents"]
    )
    with upload_tab:
        _render_upload(user_id, is_demo=is_demo)
    with profile_tab:
        _render_profile(user_id)
    with analysis_tab:
        _render_analysis(user_id)
    with documents_tab:
        _render_documents(user_id, is_demo=is_demo)


if __name__ == "__main__":
    main()
