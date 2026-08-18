"""Static SVG career-identity visualization for the dashboard."""

from html import escape
from typing import Mapping

import streamlit as st


def render_career_planet(identity: str, nodes: Mapping[str, int]) -> None:
    safe_identity = escape(identity)
    labels = {
        key: f"{escape(key)} · {int(value)}"
        for key, value in nodes.items()
    }
    st.html(
        f"""
        <div class="ct-planet-card">
          <div class="ct-section-heading">
            <div><h2>Your career planet</h2><p>A living view of your professional identity</p></div>
          </div>
          <svg class="ct-planet" viewBox="0 0 680 430" role="img" aria-label="Career identity orbited by skills, goals, interests, and experiences">
            <defs>
              <radialGradient id="ctCore" cx="32%" cy="28%" r="74%">
                <stop offset="0%" stop-color="#8ee1ef"/>
                <stop offset="44%" stop-color="#6786ec"/>
                <stop offset="100%" stop-color="#5143a8"/>
              </radialGradient>
              <filter id="ctGlow"><feGaussianBlur stdDeviation="16"/></filter>
            </defs>
            <ellipse cx="340" cy="222" rx="248" ry="132" class="ct-orbit-line" transform="rotate(-8 340 222)"/>
            <ellipse cx="340" cy="222" rx="205" ry="176" class="ct-orbit-line ct-orbit-muted" transform="rotate(18 340 222)"/>
            <circle cx="340" cy="232" r="118" fill="#7988df" opacity=".16" filter="url(#ctGlow)"/>
            <circle cx="340" cy="222" r="104" fill="url(#ctCore)"/>
            <path d="M273 181 Q323 126 399 160 Q438 183 421 239 Q394 293 324 310 Q258 278 250 224Z" fill="#4c57b9" opacity=".30"/>
            <text x="340" y="214" text-anchor="middle" class="ct-planet-title">Career identity</text>
            <text x="340" y="239" text-anchor="middle" class="ct-planet-subtitle">{safe_identity}</text>
            <g class="ct-orbit-node"><circle cx="117" cy="128" r="34"/><text x="117" y="133">{labels.get("Skills", "Skills · 0")}</text></g>
            <g class="ct-orbit-node ct-node-purple"><circle cx="555" cy="111" r="34"/><text x="555" y="116">{labels.get("Goals", "Goals · 0")}</text></g>
            <g class="ct-orbit-node ct-node-mint"><circle cx="573" cy="302" r="39"/><text x="573" y="307">{labels.get("Interests", "Interests · 0")}</text></g>
            <g class="ct-orbit-node ct-node-peach"><circle cx="121" cy="309" r="43"/><text x="121" y="314">{labels.get("Experiences", "Experiences · 0")}</text></g>
          </svg>
        </div>
        """
    )
