"""CareerTrace visual theme loader."""

from pathlib import Path

import streamlit as st


THEME_PATH = Path(__file__).with_name("theme.css")


def load_theme() -> None:
    st.html(f"<style>{THEME_PATH.read_text(encoding='utf-8')}</style>")
