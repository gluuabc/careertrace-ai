"""Presentation helpers for document onboarding and storage."""

from html import escape
from typing import Any

import streamlit as st


def _text(value: object) -> str:
    return escape(str(value or ""))


def render_documents_header() -> None:
    st.html(
        """
        <header class="ct-documents-header">
          <div><div class="ct-eyebrow">Private career evidence</div>
          <h1>Documents</h1>
          <p>Upload, analyze, and manage the private documents that support your CareerTrace profile.</p>
          <div class="ct-documents-trust"><span aria-hidden="true">⌾</span> Documents are private S3 objects. SQL stores their metadata and profile-version relationships.</div></div>
          <div class="ct-document-flow-art" aria-hidden="true"><span class="ct-doc-file">PDF</span><span class="ct-doc-core">Career<br/>identity</span><span class="ct-doc-file">DOCX</span></div>
        </header>
        """
    )


def render_demo_assets_intro() -> None:
    st.html(
        """
        <div class="ct-demo-assets-intro">
          <div><div class="ct-eyebrow">Example assets</div>
          <h2>Synthetic files for product testing</h2>
          <p>These user-provided examples are synthetic. The alumni CSV does not represent public verified alumni records.</p></div>
          <div class="ct-demo-assets-art" aria-hidden="true">◇</div>
        </div>
        """
    )


def render_documents_section(title: str, description: str) -> None:
    st.html(
        f'<div class="ct-documents-section-heading"><h2>{_text(title)}</h2>'
        f'<p>{_text(description)}</p></div>'
    )


def render_document_metadata(document: dict[str, Any]) -> None:
    """Render only persisted document metadata; action controls stay native."""

    document_type = str(document.get("document_type") or "other").replace("_", " ")
    versions = document.get("profile_versions") or []
    version_copy = ", ".join(f"v{number}" for number in versions) or "No linked profile version"
    size_kib = float(document.get("size_bytes") or 0) / 1024
    st.html(
        f"""
        <div class="ct-stored-document-meta">
          <span class="ct-document-type-icon">{_text(document_type[:3].upper())}</span>
          <div><h3>{_text(document.get('filename') or 'Document')}</h3>
          <p>{_text(document_type)} · {size_kib:.1f} KiB · uploaded {_text(document.get('uploaded_at') or 'unknown')}</p>
          <small>Related profile versions: {_text(version_copy)}</small></div>
        </div>
        """
    )
