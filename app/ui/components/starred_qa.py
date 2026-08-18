"""Presentation helpers for persisted Starred Q&A pairs."""

from html import escape
from typing import Any

import streamlit as st


def _text(value: object) -> str:
    return escape(str(value or ""))


def render_starred_qa_header(count: int) -> None:
    st.html(
        f"""
        <header class="ct-starred-header">
          <div><div class="ct-eyebrow">Saved guidance</div><h1>Starred Q&amp;A</h1>
          <p>Return to the CareerTrace answers that mattered most.</p>
          <span class="ct-starred-help">☆ Star an assistant response in Career Assistant to save its question and answer here.</span></div>
          <div class="ct-starred-count"><span>☆</span><strong>{int(count)}</strong><small>saved answer{'s' if count != 1 else ''}</small></div>
        </header>
        """
    )


def render_starred_empty_state() -> None:
    st.html(
        """
        <section class="ct-starred-empty"><div class="ct-starred-empty-icon">☆</div>
        <h2>No starred Q&amp;A yet</h2><p>Star useful CareerTrace responses from a conversation to save them here.</p></section>
        """
    )


def render_starred_qa_content(pair: dict[str, Any]) -> None:
    """Render the saved pair while keeping answer Markdown functional."""

    preference = str(pair.get("preference_update_summary") or "").strip()
    st.html(
        f"""
        <div class="ct-starred-pair-content">
          <div class="ct-starred-question-label">Your question</div>
          <h2>{_text(pair.get('question') or '')}</h2>
          <div class="ct-starred-answer-label">CareerTrace answer</div>
        </div>
        """
    )
    st.markdown(str(pair.get("answer") or ""))
    if preference:
        st.html(
            f'<div class="ct-starred-preference"><strong>Preference updated</strong>'
            f'<span>{_text(preference)}</span></div>'
        )
    st.html(
        f"""
          <div class="ct-starred-meta"><span>From conversation · {_text(pair.get('conversation_title') or 'Untitled')}</span><span>Saved {_text(pair.get('created_at') or 'unknown')}</span></div>
        """
    )
