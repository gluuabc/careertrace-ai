"""Application navigation built from native Streamlit widgets."""

from dataclasses import dataclass

import streamlit as st


@dataclass(frozen=True)
class NavigationItem:
    key: str
    label: str
    icon: str


NAVIGATION_ITEMS = (
    NavigationItem("Dashboard", "Dashboard", "⌂"),
    NavigationItem("Memory Universe", "Memory Universe", "◎"),
    NavigationItem("My profile", "My profile", "○"),
    NavigationItem("Documents", "Documents", "□"),
    NavigationItem("Career Assistant", "Career Assistant", "◇"),
    NavigationItem("Starred Q&A", "Starred Q&A", "☆"),
)


def render_sidebar_navigation() -> str:
    labels = {item.key: f"{item.icon}  {item.label}" for item in NAVIGATION_ITEMS}
    with st.sidebar:
        st.html(
            """
            <div class="ct-brand">
              <span class="ct-brand-mark" aria-hidden="true">CT</span>
              <span>CareerTrace</span>
            </div>
            <div class="ct-nav-label">Workspace</div>
            """
        )
        selected = st.radio(
            "CareerTrace navigation",
            options=[item.key for item in NAVIGATION_ITEMS],
            format_func=labels.__getitem__,
            key="careertrace_page",
            label_visibility="collapsed",
        )
    return str(selected)
