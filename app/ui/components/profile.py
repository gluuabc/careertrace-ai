"""Data-agnostic presentation components for the structured Profile page."""

from html import escape
from typing import Any, Iterable

import streamlit as st


def _text(value: object) -> str:
    return escape(str(value or ""))


def _clean(values: Iterable[object] | None) -> list[str]:
    return [str(value).strip() for value in (values or []) if str(value).strip()]


def _initials(value: object) -> str:
    words = [word for word in str(value or "").split() if word]
    return "".join(word[0] for word in words[:2]).upper() or "CT"


def _chips(values: Iterable[object] | None, *, empty: str) -> str:
    cleaned = _clean(values)
    if not cleaned:
        return f'<span class="ct-profile-empty">{_text(empty)}</span>'
    return "".join(
        f'<span class="ct-profile-chip">{_text(value)}</span>' for value in cleaned
    )


def _item_list(
    values: Iterable[tuple[object, object]], *, empty: str, limit: int = 3
) -> str:
    cleaned = [
        (str(title).strip(), str(detail).strip())
        for title, detail in values
        if str(title).strip() or str(detail).strip()
    ]
    if not cleaned:
        return f'<div class="ct-profile-empty">{_text(empty)}</div>'
    items = "".join(
        f"<li><strong>{_text(title or detail)}</strong>"
        f"{f'<span>{_text(detail)}</span>' if title and detail else ''}</li>"
        for title, detail in cleaned[:limit]
    )
    remainder = len(cleaned) - limit
    more = (
        f'<div class="ct-profile-more">+{remainder} more in your editable profile</div>'
        if remainder > 0
        else ""
    )
    return f'<ul class="ct-profile-list">{items}</ul>{more}'


def render_profile_identity(
    account: dict[str, Any], profile: dict[str, Any]
) -> None:
    """Render only persisted identity and current Profile metadata."""

    display_name = profile.get("name") or account.get("name") or "CareerTrace user"
    target_roles = _clean(profile.get("target_roles"))
    direction = target_roles[0] if target_roles else profile.get("career_goal")
    source_documents = list(profile.get("source_documents") or [])
    source_copy = (
        ", ".join(str(item.get("filename") or "document") for item in source_documents)
        if source_documents
        else "No linked source document"
    )
    version = profile.get("profile_version")
    version_copy = f"Profile v{version}" if version else "Current profile"
    index_status = str(profile.get("retrieval_index_status") or "not recorded")

    st.html(
        f"""
        <section class="ct-profile-hero">
          <div class="ct-profile-avatar" aria-hidden="true">{_text(_initials(display_name))}</div>
          <div class="ct-profile-hero-copy">
            <div class="ct-eyebrow">Career identity core</div>
            <h2>{_text(display_name)}</h2>
            <div class="ct-profile-facts">
              <div><span>University</span><strong>{_text(profile.get('school') or 'Not provided')}</strong></div>
              <div><span>Major</span><strong>{_text(profile.get('major') or 'Not provided')}</strong></div>
              <div><span>Graduation</span><strong>{_text(profile.get('graduation_year') or 'Not provided')}</strong></div>
              <div><span>Career direction</span><strong>{_text(direction or 'Not set')}</strong></div>
            </div>
            <div class="ct-profile-provenance">
              <span>{_text(version_copy)}</span><span>{_text(source_copy)}</span><span>Retrieval: {_text(index_status.replace('_', ' '))}</span>
            </div>
          </div>
          <div class="ct-profile-hero-art" aria-hidden="true">
            <span class="ct-profile-mountain ct-mountain-one"></span>
            <span class="ct-profile-mountain ct-mountain-two"></span>
            <span class="ct-profile-flag"></span>
          </div>
        </section>
        """
    )


def render_profile_orbit(profile: dict[str, Any]) -> None:
    """Render a static identity-core map from canonical Profile collections."""

    education_count = len(_clean(profile.get("education"))) or int(
        bool(profile.get("school"))
    )
    direction_count = len(_clean(profile.get("target_roles"))) or int(
        bool(profile.get("career_goal"))
    )
    counts = {
        "Education": education_count,
        "Skills": len(_clean(profile.get("skills"))),
        "Experience": len(list(profile.get("experience") or [])),
        "Projects": len(list(profile.get("projects") or [])),
        "Direction": direction_count,
    }
    core_label = (
        (_clean(profile.get("target_roles")) or [None])[0]
        or profile.get("major")
        or "Structured identity"
    )

    st.html(
        f"""
        <section class="ct-profile-orbit" role="img" aria-label="Structured profile identity core with education, skills, experience, projects, and career direction">
          <div class="ct-profile-orbit-line ct-profile-orbit-outer"></div>
          <div class="ct-profile-orbit-line ct-profile-orbit-inner"></div>
          <div class="ct-profile-core-glow"></div>
          <div class="ct-profile-poly-core" aria-hidden="true">
            <span></span><span></span><span></span>
          </div>
          <div class="ct-profile-core-copy"><strong>Profile core</strong><span>{_text(core_label)}</span></div>
          <div class="ct-profile-node ct-profile-node-education"><i>ED</i><span>Education · {counts['Education']}</span></div>
          <div class="ct-profile-node ct-profile-node-skills"><i>SK</i><span>Skills · {counts['Skills']}</span></div>
          <div class="ct-profile-node ct-profile-node-experience"><i>EX</i><span>Experience · {counts['Experience']}</span></div>
          <div class="ct-profile-node ct-profile-node-projects"><i>PR</i><span>Projects · {counts['Projects']}</span></div>
          <div class="ct-profile-node ct-profile-node-direction"><i>GO</i><span>Direction · {counts['Direction']}</span></div>
        </section>
        """
    )


def render_profile_summary(profile: dict[str, Any]) -> None:
    """Render a read-only summary of fields already editable in Profile."""

    education = [
        (
            profile.get("school") or "Education",
            " · ".join(
                str(value)
                for value in (profile.get("major"), profile.get("graduation_year"))
                if value
            ),
        )
    ]
    education.extend((item, "") for item in _clean(profile.get("education")))
    experience = [
        (
            item.get("role") or item.get("organization") or "Experience",
            " · ".join(
                value
                for value in (
                    str(item.get("organization") or "").strip(),
                    str(item.get("description") or "").strip(),
                )
                if value
            ),
        )
        for item in (profile.get("experience") or [])
        if isinstance(item, dict)
    ]
    projects = [
        (item.get("title") or "Project", item.get("description") or "")
        for item in (profile.get("projects") or [])
        if isinstance(item, dict)
    ]
    credentials = [
        *((value, "Course") for value in _clean(profile.get("courses"))),
        *((value, "Certification") for value in _clean(profile.get("certifications"))),
        *((value, "Achievement") for value in _clean(profile.get("achievements"))),
    ]

    st.html(
        f"""
        <section class="ct-profile-summary-grid">
          <article class="ct-profile-summary-card ct-profile-accent-purple">
            <div class="ct-profile-card-icon">ED</div><h3>Education</h3>
            {_item_list(education, empty='Add education details')}
          </article>
          <article class="ct-profile-summary-card ct-profile-accent-blue">
            <div class="ct-profile-card-icon">SK</div><h3>Skills</h3>
            <div class="ct-profile-chips">{_chips(profile.get('skills'), empty='Add skills')}</div>
          </article>
          <article class="ct-profile-summary-card ct-profile-accent-mint">
            <div class="ct-profile-card-icon">EX</div><h3>Experience</h3>
            {_item_list(experience, empty='Add experience')}
          </article>
          <article class="ct-profile-summary-card ct-profile-accent-blue">
            <div class="ct-profile-card-icon">PR</div><h3>Projects</h3>
            {_item_list(projects, empty='Add projects')}
          </article>
          <article class="ct-profile-summary-card ct-profile-accent-peach">
            <div class="ct-profile-card-icon">＋</div><h3>Credentials</h3>
            {_item_list(credentials, empty='Add courses, certifications, or achievements')}
          </article>
        </section>
        """
    )


def render_career_direction(profile: dict[str, Any]) -> None:
    """Render supported career-preference fields without inventing a biography."""

    target_roles = _clean(profile.get("target_roles"))
    details = [
        ("Preferred locations", _clean(profile.get("preferred_locations"))),
        ("Employment types", _clean(profile.get("employment_types"))),
        ("Work mode", [profile.get("remote_preference")] if profile.get("remote_preference") else []),
        ("Work authorization", [profile.get("work_authorization")] if profile.get("work_authorization") else []),
    ]
    detail_html = "".join(
        f'<div><span>{_text(label)}</span><strong>{_text(" · ".join(values) if values else "Not set")}</strong></div>'
        for label, values in details
    )

    st.html(
        f"""
        <section class="ct-profile-direction-card">
          <div class="ct-profile-direction-main">
            <div class="ct-eyebrow">Career direction</div>
            <h3>{_text(profile.get('career_goal') or 'Add a career goal to clarify your direction.')}</h3>
            <div class="ct-profile-chips">{_chips(target_roles, empty='No target roles set')}</div>
          </div>
          <div class="ct-profile-direction-details">{detail_html}</div>
          <div class="ct-profile-direction-art" aria-hidden="true"><span></span></div>
        </section>
        """
    )
