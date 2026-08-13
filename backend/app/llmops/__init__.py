"""LLM operations layer: model routing, tracing, cost tracking, prompt registry.

Every LLM call in the system is routed through this package's model_router,
traced through its tracer, and cost-checked through its cost_tracker, all
invoked from the harness's run_step() — never directly from a tool or agent
(CLAUDE.md non-negotiable #1).
"""

from __future__ import annotations
