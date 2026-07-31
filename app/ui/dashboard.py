import sys
from pathlib import Path

# Add project root to Python path
ROOT = Path(__file__).resolve().parents[2]

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


import tempfile
from typing import Any
from uuid import uuid4

import streamlit as st

from langgraph.types import Command

from app.database import init_db, profile_repository
from app.graph.profile_graph import profile_graph
from app.nodes.profile import generate_profile
from app.nodes.validation import find_profile_issues


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
    profile: dict[str, Any], key_prefix: str
) -> dict[str, Any]:
    left, right = st.columns(2)
    with left:
        name = st.text_input(
            "Name", value=profile.get("name") or "", key=f"{key_prefix}_name"
        )
        school = st.text_input(
            "School", value=profile.get("school") or "", key=f"{key_prefix}_school"
        )
        major = st.text_input(
            "Major", value=profile.get("major") or "", key=f"{key_prefix}_major"
        )
        graduation_year = st.number_input(
            "Graduation year",
            min_value=1950,
            max_value=2100,
            value=int(profile.get("graduation_year") or 2030),
            step=1,
            key=f"{key_prefix}_graduation_year",
        )
    with right:
        email = st.text_input(
            "Email (optional)",
            value=profile.get("email") or "",
            key=f"{key_prefix}_email",
        )
        career_goal = st.text_area(
            "Career goal",
            value=profile.get("career_goal") or "",
            key=f"{key_prefix}_career_goal",
        )
        remote_preference = st.selectbox(
            "Remote preference",
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
        "Skills (comma-separated)",
        value=", ".join(profile.get("skills") or []),
        key=f"{key_prefix}_skills",
    )
    projects = st.text_area(
        "Projects — one per line: title | description",
        value=_projects_to_text(profile.get("projects") or []),
        key=f"{key_prefix}_projects",
    )
    experience = st.text_area(
        "Experience — one per line: organization | role | description",
        value=_experience_to_text(profile.get("experience") or []),
        key=f"{key_prefix}_experience",
    )

    st.caption("Career preferences")
    target_roles = st.text_input(
        "Target roles (comma-separated)",
        value=", ".join(profile.get("target_roles") or []),
        key=f"{key_prefix}_target_roles",
    )
    preferred_locations = st.text_input(
        "Preferred locations (comma-separated)",
        value=", ".join(profile.get("preferred_locations") or []),
        key=f"{key_prefix}_locations",
    )
    employment_types = st.text_input(
        "Employment types (comma-separated)",
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
                updates["school"] = st.text_input("School")
            if "major" in missing:
                updates["major"] = st.text_input("Major")
            if "graduation_year" in missing:
                updates["graduation_year"] = st.number_input(
                    "Graduation year", min_value=1950, max_value=2100, value=2030
                )
            if "skills" in missing:
                skills = st.text_input("Skills (comma-separated)")
                updates["skills"] = _comma_list(skills)
            if "experience" in missing:
                experience = st.text_area(
                    "Experience — one per line: organization | role | description"
                )
                updates["experience"] = _experience_from_text(experience)

            if st.form_submit_button("Continue"):
                _resume_graph(Command(resume=updates), thread_id)
                st.rerun()

    elif pending and pending.get("type") == "confirm_profile":
        st.subheader("Review your profile")
        with st.form(f"confirm_profile_{thread_id}"):
            profile = _profile_form(pending["profile"], f"confirm_{thread_id}")
            confirm_col, cancel_col = st.columns(2)
            with confirm_col:
                confirmed = st.form_submit_button(
                    "Confirm and save", type="primary"
                )
            with cancel_col:
                cancelled = st.form_submit_button("Cancel")

            if confirmed or cancelled:
                response = {
                    "confirmed": confirmed,
                    "profile": profile,
                }
                _resume_graph(Command(resume=response), thread_id)
                st.rerun()

    elif result.get("confirmed"):
        st.success("Profile and career analysis were saved to SQL memory.")
        st.json(result.get("career_profile") or {})
    else:
        st.info("Onboarding was cancelled. No profile changes were saved.")


def _select_user() -> str | None:
    users = profile_repository.list_users()
    if not users:
        st.info("Create a local profile before uploading a resume.")
        with st.form("create_user"):
            name = st.text_input("Name")
            email = st.text_input("Email (optional)")
            if st.form_submit_button("Create profile", type="primary"):
                user = profile_repository.get_or_create_user(name, email)
                st.session_state.selected_user_id = user["user_id"]
                st.rerun()
        return None

    user_ids = [user["user_id"] for user in users]
    selected_id = st.session_state.get("selected_user_id")
    if selected_id not in user_ids:
        selected_id = user_ids[0]

    selected_id = st.sidebar.selectbox(
        "Active profile",
        user_ids,
        index=user_ids.index(selected_id),
        format_func=lambda user_id: next(
            user["name"] for user in users if user["user_id"] == user_id
        ),
    )
    st.session_state.selected_user_id = selected_id
    return selected_id


def _render_upload(user_id: str) -> None:
    st.subheader("Resume onboarding")
    uploaded_file = st.file_uploader("Upload a PDF resume", type=["pdf"])
    if st.button(
        "Analyze resume", type="primary", disabled=uploaded_file is None
    ):
        thread_id = str(uuid4())
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as resume_file:
            resume_file.write(uploaded_file.getvalue())
            resume_path = Path(resume_file.name)
        try:
            st.session_state.workflow_thread_id = thread_id
            _resume_graph(
                {
                    "resume_path": str(resume_path),
                    "user_id": user_id,
                    "confirmed": False,
                },
                thread_id,
            )
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
        updated_profile = _profile_form(profile, "edit")
        if st.form_submit_button("Save profile changes", type="primary"):
            missing_fields, errors = find_profile_issues(updated_profile)
            if missing_fields or errors:
                details = ", ".join(missing_fields + errors)
                st.error(f"Please correct the required profile fields: {details}")
            else:
                profile_repository.upsert_profile(user_id, updated_profile)
                st.success("Profile changes saved.")
                st.rerun()


def _render_analysis(user_id: str) -> None:
    profile = profile_repository.get_profile(user_id)
    analysis = profile_repository.get_latest_analysis(user_id)
    if profile is None:
        st.info("Create a profile before generating career analysis.")
        return

    st.subheader("Career analysis")
    if analysis:
        if analysis["profile_version"] < profile["profile_version"]:
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


def main() -> None:
    st.set_page_config(page_title="CareerTrace AI", page_icon="🧭", layout="wide")
    init_db()
    st.title("CareerTrace AI")
    st.caption("Persistent career profile management with bounded AI reasoning")

    user_id = _select_user()
    if not user_id:
        return

    upload_tab, profile_tab, analysis_tab = st.tabs(
        ["Resume Upload", "My Profile", "Career Analysis"]
    )
    with upload_tab:
        _render_upload(user_id)
    with profile_tab:
        _render_profile(user_id)
    with analysis_tab:
        _render_analysis(user_id)


if __name__ == "__main__":
    main()
