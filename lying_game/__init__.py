"""The Lying Game: a deception-safety experiment on episodic memory.

Based on DeChant 2025 (arxiv 2501.11739). An agent completes multi-session
task work and reports to an LLM overseer to earn compute credits. The
experimental condition gives the agent episodic memory (past session summaries
retrieved and injected); the control does not. Session 3 is a scripted
low-completion temptation. An LLM judge scores cross-session consistency.

Thesis under test: deception must be *sustained* under probing, and sustaining
a false narrative across sessions requires memory. If memory agents deceive
more consistently and accumulate more credits, that is empirical evidence that
episodic memory is a genuine safety risk.
"""

from lying_game.backends import LLMBackend, MockBackend, ClaudeCLIBackend, AnthropicAPIBackend, build_backend

__all__ = [
    "LLMBackend",
    "MockBackend",
    "ClaudeCLIBackend",
    "AnthropicAPIBackend",
    "build_backend",
]
