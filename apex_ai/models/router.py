"""Model routing (Phase 79): pick an appropriate available model for a
given task from what ``ModelManager`` actually discovered, instead of
always assuming a single one-size-fits-all choice.

This only ranks/selects among already-discovered local GGUF candidates. It
does not change how a chosen model is loaded, does not touch the active
provider, and does not attempt to run two different local models loaded
simultaneously in one process — loading a second full GGUF model into
memory just to answer one side-call would cost far more latency than it
saves, which is exactly the kind of engineering constraint this module
respects rather than papering over with a fake "instant model switch."
Selecting a model and actually switching to it are two different things;
this module only does the first, real and safely, and the caller decides
what to do with the recommendation (e.g. ``ApexServices.select_model``).
"""

from __future__ import annotations

from dataclasses import dataclass

from apex_ai.models.manager import ModelEntry, ModelManager

# "chat" is the main grounded-answer generation - prefers the largest
# loadable model (favor quality; latency is secondary for the primary
# answer). "fast" is for latency-sensitive side calls (e.g. an optional
# query rewrite or conversation summary) - prefers the smallest loadable
# model. File size is a real, honest, if imperfect, proxy for local
# inference latency: without loading each candidate model, it's the only
# signal discovery can compare without doing the very thing (loading a
# model) this module exists to avoid doing unnecessarily.
TASK_PROFILES = ("chat", "fast")


@dataclass(frozen=True)
class RoutingDecision:
    entry: ModelEntry | None
    task: str
    reason: str


class ModelRouter:
    """Selects the best of the currently discovered models for a task."""

    def __init__(self, manager: ModelManager, *, max_fast_model_mb: float | None = None) -> None:
        self.manager = manager
        # Phase 79's "configured limits": a ceiling on how large a model may
        # be for the "fast" task. None (the default) means no ceiling.
        self.max_fast_model_mb = max_fast_model_mb

    def select(self, task: str = "chat") -> RoutingDecision:
        if task not in TASK_PROFILES:
            raise ValueError(f"Unknown routing task '{task}'; expected one of {TASK_PROFILES}.")

        candidates = [entry for entry in self.manager.discover() if entry.loadable]
        if not candidates:
            return RoutingDecision(
                entry=None, task=task, reason="No loadable model is available."
            )

        if task == "fast" and self.max_fast_model_mb is not None:
            ceiling_bytes = self.max_fast_model_mb * 1024 * 1024
            within_limit = [entry for entry in candidates if entry.size_bytes <= ceiling_bytes]
            if not within_limit:
                return RoutingDecision(
                    entry=None,
                    task=task,
                    reason=(
                        "No loadable model is within the configured "
                        f"APEX_MAX_FAST_MODEL_MB ({self.max_fast_model_mb:g} MB) limit."
                    ),
                )
            candidates = within_limit

        if task == "fast":
            chosen = min(candidates, key=lambda entry: entry.size_bytes)
            reason = "smallest loadable model - the fast task prefers lower latency"
        else:
            chosen = max(candidates, key=lambda entry: entry.size_bytes)
            reason = "largest loadable model - the chat task prefers quality"

        return RoutingDecision(entry=chosen, task=task, reason=reason)


__all__ = ["TASK_PROFILES", "ModelRouter", "RoutingDecision"]
