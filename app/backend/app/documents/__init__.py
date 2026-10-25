"""공문 초안 (official-letter drafts) generated locally from templates with the recommendation's evidence."""

from .docx import build_docx
from .templates import ACTION_DOCS, render

__all__ = ["ACTION_DOCS", "build_docx", "render"]
