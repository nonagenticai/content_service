"""LLM provider abstraction layer.

All calls route through worker_management (WM) — the sole LLM gateway for
w2v services. See :mod:`src.llm.wm_provider`.
"""

from src.llm.types import LLMProvider, LLMRequest, LLMResponse
from src.llm.wm_provider import WMLLMProvider

__all__ = ["LLMProvider", "LLMRequest", "LLMResponse", "WMLLMProvider"]
