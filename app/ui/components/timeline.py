"""Career-event timeline rendering without repository access."""

from datetime import datetime
from html import escape
from typing import Any, Iterable

import streamlit as st


def _event_date(event: dict[str, Any]) -> str:
    raw = event.get("event_time") or event.get("start_date") or event.get("created_at")
    if not raw:
        return "Date unknown"
    try:
        parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        return parsed.strftime("%b %d, %Y")
    except ValueError:
        return str(raw)


def render_timeline(events: Iterable[dict[str, Any]], *, limit: int | None = None) -> None:
    rows = list(events)
    if limit is not None:
        rows = rows[:limit]
    if not rows:
        st.html(
            '<div class="ct-empty-state">Career events will appear here after you approve them.</div>'
        )
        return

    items: list[str] = []
    for event in rows:
        title = escape(str(event.get("title") or event.get("content") or "Career event"))
        description = escape(str(event.get("description") or event.get("content") or ""))
        impact = escape(str(event.get("outcome") or ""))
        date = escape(_event_date(event))
        status = escape(str(event.get("event_status") or "unknown"))
        impact_html = f'<div class="ct-event-impact">Impact · {impact}</div>' if impact else ""
        items.append(
            f"""
            <li class="ct-timeline-item">
              <div class="ct-timeline-dot"></div>
              <div class="ct-event-date">{date}</div>
              <div class="ct-event-copy">
                <div class="ct-event-title">{title}</div>
                <div class="ct-event-description">{description}</div>
                {impact_html}
              </div>
              <span class="ct-status-pill">{status}</span>
            </li>
            """
        )
    st.html(f'<ol class="ct-timeline">{"".join(items)}</ol>')
