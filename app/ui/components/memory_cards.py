"""Reusable presentation for approved semantic memories."""

from html import escape
from typing import Any

import streamlit as st


GROUP_LABELS = {
    "skill": "Skills",
    "skills": "Skills",
    "goal": "Goals",
    "goals": "Goals",
    "interest": "Interests",
    "interests": "Interests",
    "preference": "Preferences",
    "preferences": "Preferences",
    "work_style": "Work style",
}


def _month(value: object) -> str:
    from datetime import datetime

    if not value:
        return "Not recorded"
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).strftime("%b %Y")
    except ValueError:
        return str(value)


def _metadata(item: dict[str, Any]) -> str:
    confidence = item.get("confidence")
    confidence_text = str(confidence) if confidence is not None else "Not recorded"
    source = str(item.get("source") or "Not recorded")
    return (
        f'<dl class="ct-memory-meta">'
        f'<div><dt>Confidence</dt><dd>{escape(confidence_text)}</dd></div>'
        f'<div><dt>Source</dt><dd>{escape(source)}</dd></div>'
        f'<div><dt>Updated</dt><dd>{escape(_month(item.get("created_at")))}</dd></div>'
        f'</dl>'
    )


def render_memory_card(item: dict[str, Any]) -> None:
    value = escape(str(item.get("value") or item.get("content") or ""))
    topic = escape(str(item.get("topic_key") or "General"))
    source = escape(str(item.get("source") or "unknown source"))
    st.html(
        f"""
        <div class="ct-memory-card">
          <div class="ct-memory-value">{value}</div>
          <div class="ct-metadata">{topic} · {source}</div>
        </div>
        """
    )
    if item.get("retrieval_index_status") == "failed":
        st.warning("Saved in SQL; retrieval indexing needs retry.")


def render_semantic_memory_panel(memories: list[dict[str, Any]]) -> None:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in memories:
        raw_group = str(item.get("semantic_group") or "context").casefold()
        label = GROUP_LABELS.get(raw_group, raw_group.replace("_", " ").title())
        grouped.setdefault(label, []).append(item)

    preferred = ("Skills", "Goals", "Interests", "Preferences", "Work style")
    labels = [label for label in preferred if label in grouped]
    labels.extend(sorted(label for label in grouped if label not in labels))
    if not labels:
        st.html(
            '<div class="ct-empty-state">Approved semantic memories will appear here after review.</div>'
        )
        return

    cards: list[str] = []
    for label in labels:
        items: list[str] = []
        for item in grouped[label][:6]:
            value = escape(str(item.get("value") or item.get("content") or ""))
            topic = escape(str(item.get("topic_key") or "General"))
            items.append(
                f'<li><strong>{value}</strong><span>{topic}</span>{_metadata(item)}</li>'
            )
        remainder = len(grouped[label]) - len(items)
        remainder_html = (
            f'<div class="ct-card-remainder">+ {remainder} more</div>' if remainder else ""
        )
        cards.append(
            f"""
            <article class="ct-semantic-group-card">
              <div class="ct-semantic-card-heading"><span class="ct-node-icon">◇</span><h3>{escape(label)}</h3></div>
              <ul>{''.join(items)}</ul>{remainder_html}
            </article>
            """
        )
    st.html(f'<div class="ct-semantic-grid">{"".join(cards)}</div>')


def render_memory_candidate_summary(candidate: dict[str, Any]) -> None:
    kind = str(candidate.get("memory_kind") or "legacy")
    if kind == "semantic":
        label = str(candidate.get("semantic_group") or "Semantic memory").replace("_", " ").title()
        content = candidate.get("proposed_value") or candidate.get("content")
    elif kind == "episodic":
        label = "Career event"
        content = candidate.get("content")
    else:
        label = str(candidate.get("category") or "Memory").replace("_", " ").title()
        content = candidate.get("content")
    st.html(
        f"""
        <div class="ct-inbox-candidate">
          <div class="ct-inbox-kicker">AI detected · {escape(label)}</div>
          <div class="ct-inbox-content">{escape(str(content or ''))}</div>
          <div class="ct-metadata">Source · {escape(str(candidate.get('source') or 'Not recorded'))}</div>
        </div>
        """
    )
