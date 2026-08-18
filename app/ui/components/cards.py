"""Small HTML card primitives with no data-access responsibilities."""

from html import escape

import streamlit as st


def _text(value: object) -> str:
    return escape(str(value or ""))


def render_page_header(title: str, subtitle: str, *, eyebrow: str | None = None) -> None:
    eyebrow_html = (
        f'<div class="ct-eyebrow">{_text(eyebrow)}</div>' if eyebrow else ""
    )
    st.html(
        f"""
        <header class="ct-page-header">
          {eyebrow_html}
          <h1>{_text(title)}</h1>
          <p>{_text(subtitle)}</p>
        </header>
        """
    )


def render_metric_card(
    label: str,
    value: str | int,
    metadata: str,
    *,
    icon: str,
    accent: str = "blue",
) -> None:
    st.html(
        f"""
        <article class="ct-card ct-metric-card ct-accent-{_text(accent)}">
          <div class="ct-card-topline">
            <span class="ct-card-label">{_text(label)}</span>
            <span class="ct-icon" aria-hidden="true">{_text(icon)}</span>
          </div>
          <div class="ct-metric-value">{_text(value)}</div>
          <div class="ct-metadata">{_text(metadata)}</div>
        </article>
        """
    )


def render_memory_page_header(
    *, total_memories: int, career_events: int, pending_reviews: int
) -> None:
    st.html(
        f"""
        <section class="ct-memory-header-grid">
          <header class="ct-memory-header-copy">
            <div class="ct-eyebrow">CareerTrace memory system</div>
            <h1>Memory Universe</h1>
            <p>Your AI memory system that learns your skills, goals, experiences, and career journey.</p>
          </header>
          <article class="ct-memory-stat"><span>Total memories</span><strong>{int(total_memories)}</strong><small>Approved semantic + episodic memory</small></article>
          <article class="ct-memory-stat"><span>Career events</span><strong>{int(career_events)}</strong><small>Approved episodic records</small></article>
          <article class="ct-memory-stat"><span>Pending reviews</span><strong>{int(pending_reviews)}</strong><small>Memory + profile suggestions</small></article>
        </section>
        """
    )


def render_information_card(
    title: str,
    body: str,
    *,
    metadata: str | None = None,
    icon: str = "→",
) -> None:
    metadata_html = (
        f'<div class="ct-metadata">{_text(metadata)}</div>' if metadata else ""
    )
    st.html(
        f"""
        <article class="ct-card ct-information-card">
          <div class="ct-card-topline">
            <h3>{_text(title)}</h3><span class="ct-soft-icon">{_text(icon)}</span>
          </div>
          <p>{_text(body)}</p>
          {metadata_html}
        </article>
        """
    )


def render_insight_card(title: str, body: str, *, label: str = "From your career context") -> None:
    st.html(
        f"""
        <article class="ct-card ct-insight-card">
          <div class="ct-eyebrow">{_text(label)}</div>
          <h3>{_text(title)}</h3>
          <p>{_text(body)}</p>
          <div class="ct-insight-art" aria-hidden="true"></div>
        </article>
        """
    )
