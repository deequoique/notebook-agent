"""Compatibility facade for the modular bounded Agent runtime.

Implementation lives in the focused state, builder, tool, answer, and
orchestration modules. Existing callers may continue importing the supported
runtime surface from this module.
"""

from .agent_builder import build_agent
from .answer_pipeline import (
    ComposerDeps,
    _append_sources,
    _compressed_citations,
    build_composer,
)
from .orchestrator import KnowledgeAgent
from .runtime_state import AgentDeps, AgentExecution

__all__ = [
    "AgentDeps",
    "AgentExecution",
    "ComposerDeps",
    "KnowledgeAgent",
    "_append_sources",
    "_compressed_citations",
    "build_agent",
    "build_composer",
]
