"""Safe, static CareerTrace memory-orbit visualization."""

from datetime import datetime
from html import escape
from typing import Any, Iterable

import streamlit as st


SEMANTIC_NODES = (
    ("Skills", {"skill", "skills"}, "ct-semantic-skills", "</>"),
    ("Goals", {"goal", "goals"}, "ct-semantic-goals", "⚑"),
    ("Interests", {"interest", "interests"}, "ct-semantic-interests", "♡"),
    ("Preferences", {"preference", "preferences"}, "ct-semantic-preferences", "≡"),
    ("Work style", {"work_style"}, "ct-semantic-work-style", "○"),
)

EPISODIC_NODES = (
    ("Research experience", ("research", "lab", "study"), "ct-event-research", "△"),
    ("Projects", ("project", "build", "hackathon"), "ct-event-projects", "</>"),
    ("Courses", ("course", "class", "certificate", "workshop"), "ct-event-courses", "◇"),
    ("Career milestones", (), "ct-event-milestones", "☆"),
)


def _month(value: object) -> str:
    if not value:
        return "Not recorded"
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.strftime("%b %Y")
    except ValueError:
        return str(value)


def _tooltip(title: str, item: dict[str, Any] | None, *, episodic: bool = False) -> str:
    if item is None:
        return (
            f'<div class="ct-node-tooltip"><strong>{escape(title)}</strong>'
            '<span>No approved memory yet</span></div>'
        )
    name = item.get("title") if episodic else item.get("value")
    name = name or item.get("content") or title
    confidence = item.get("confidence")
    confidence_text = str(confidence) if confidence is not None else "Not recorded"
    source = str(item.get("source") or "Not recorded")
    updated = _month(
        item.get("event_time")
        or item.get("start_date")
        or item.get("created_at")
    )
    return f"""
      <div class="ct-node-tooltip">
        <strong>{escape(str(name))}</strong>
        <span>Confidence · {escape(confidence_text)}</span>
        <span>Source · {escape(source)}</span>
        <span>Updated · {escape(updated)}</span>
      </div>
    """


def _semantic_nodes(memories: list[dict[str, Any]]) -> str:
    nodes: list[str] = []
    for label, groups, css_class, icon in SEMANTIC_NODES:
        matching = [
            item
            for item in memories
            if str(item.get("semantic_group") or "").casefold() in groups
        ]
        nodes.append(
            f"""
            <div class="ct-universe-node {css_class}" tabindex="0">
              <span class="ct-node-icon">{escape(icon)}</span>
              <span class="ct-node-label">{escape(label)} · {len(matching)}</span>
              {_tooltip(label, matching[0] if matching else None)}
            </div>
            """
        )
    return "".join(nodes)


def _event_nodes(events: list[dict[str, Any]]) -> str:
    available = list(events)
    nodes: list[str] = []
    for label, terms, css_class, icon in EPISODIC_NODES:
        if terms:
            matching = [
                item
                for item in available
                if any(
                    term in str(item.get("title") or item.get("content") or "").casefold()
                    for term in terms
                )
            ]
        else:
            matching = available
        nodes.append(
            f"""
            <div class="ct-universe-node {css_class}" tabindex="0">
              <span class="ct-node-icon">{escape(icon)}</span>
              <span class="ct-node-label">{escape(label)} · {len(matching)}</span>
              {_tooltip(label, matching[0] if matching else None, episodic=True)}
            </div>
            """
        )
    return "".join(nodes)


def render_memory_universe(
    semantic_memories: Iterable[dict[str, Any]],
    career_events: Iterable[dict[str, Any]],
) -> None:
    """Render a CSS/SVG-only universe; every dynamic value is HTML escaped."""

    semantic_rows = list(semantic_memories)
    event_rows = list(career_events)
    st.html(
        f"""
        <section class="ct-memory-universe" aria-label="Career identity memory universe">
          <div class="ct-orbit-copy ct-semantic-copy">
            <strong>Semantic memory orbit</strong><span>What AI knows about you</span>
          </div>
          <div class="ct-orbit-copy ct-episodic-copy">
            <strong>Episodic memory orbit</strong><span>What happened throughout your career journey</span>
          </div>
          <div class="ct-orbit ct-orbit-semantic"></div>
          <div class="ct-orbit ct-orbit-episodic"></div>
          <div class="ct-core-glow"></div>
          <svg class="ct-low-poly-core" viewBox="0 0 240 240" role="img" aria-label="Blue and purple career identity core">
            <defs>
              <radialGradient id="ct-memory-core" cx="30%" cy="24%" r="80%">
                <stop offset="0%" stop-color="#8bdaf4"/><stop offset="48%" stop-color="#6379ec"/><stop offset="100%" stop-color="#5a27b1"/>
              </radialGradient>
              <clipPath id="ct-memory-clip"><circle cx="120" cy="120" r="92"/></clipPath>
            </defs>
            <circle cx="120" cy="120" r="92" fill="url(#ct-memory-core)"/>
            <g clip-path="url(#ct-memory-clip)" opacity=".42">
              <polygon points="28,92 82,42 106,103" fill="#b7effb"/><polygon points="82,42 153,25 131,88" fill="#77baf3"/>
              <polygon points="153,25 220,73 167,105" fill="#605bd5"/><polygon points="28,92 106,103 53,153" fill="#6ea8ec"/>
              <polygon points="106,103 131,88 167,105 128,144" fill="#4b6ee0"/><polygon points="167,105 220,73 207,151" fill="#672dc2"/>
              <polygon points="53,153 128,144 92,214" fill="#5663d2"/><polygon points="128,144 207,151 165,222" fill="#4c2cad"/>
            </g>
          </svg>
          <div class="ct-core-label"><strong>Career Identity Core</strong><span>Your evolving professional identity</span></div>
          {_semantic_nodes(semantic_rows)}
          {_event_nodes(event_rows)}
          <div class="ct-universe-hint"><strong>Explore your journey</strong><span>Hover or focus on orbit nodes to inspect real memory details.</span></div>
        </section>
        """
    )
