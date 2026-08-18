"""Presentation helpers for the Career Assistant workspace."""

from html import escape

import streamlit as st


def _text(value: object) -> str:
    return escape(str(value or ""))


def render_assistant_header() -> None:
    """Render static product framing; conversations remain in the dashboard."""

    st.html(
        """
        <header class="ct-assistant-header">
          <div><div class="ct-eyebrow">Career guidance workspace</div>
          <h1>Career Assistant</h1>
          <p>Ask questions, explore career options, and take informed next steps using your saved career context.</p></div>
          <div class="ct-assistant-header-orb" aria-hidden="true"><span></span><span></span></div>
        </header>
        <div class="ct-assistant-trust"><span aria-hidden="true">⌾</span>
        <p>Conversations are saved in SQL. Chat does not automatically modify your profile or durable memory.</p></div>
        """
    )


def render_assistant_section(title: str, description: str, *, eyebrow: str | None = None) -> None:
    eyebrow_html = f'<div class="ct-eyebrow">{_text(eyebrow)}</div>' if eyebrow else ""
    st.html(
        f'<div class="ct-assistant-section-heading">{eyebrow_html}'
        f'<h2>{_text(title)}</h2><p>{_text(description)}</p></div>'
    )


def render_result_heading(title: str, description: str | None = None) -> None:
    description_html = f"<p>{_text(description)}</p>" if description else ""
    st.html(
        f'<div class="ct-result-heading"><h2>{_text(title)}</h2>{description_html}</div>'
    )


def render_agent_activity_intro() -> None:
    st.html(
        """
        <div class="ct-activity-intro">
          <div class="ct-eyebrow">Assistant activity</div>
          <p>Progress and source status for the active conversation.</p>
        </div>
        """
    )
