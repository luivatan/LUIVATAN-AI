"""User-confirmation workflow for safe long-term-memory candidates.

Pending proposals are not model context and are not confirmed memories. Only an
explicit approve operation moves one into the long-term-memory table.
"""

from __future__ import annotations

from apex_ai.memory.extraction import MemoryCandidateExtractor
from apex_ai.memory.long_term import LongTermMemory, LongTermMemoryStore, PendingMemory
from apex_ai.memory.relevance import find_similar_memory


class MemoryConfirmationService:
    def __init__(
        self,
        extractor: MemoryCandidateExtractor,
        store: LongTermMemoryStore,
    ) -> None:
        self.extractor = extractor
        self.store = store

    def find_conflict(self, user_id: str, candidate: PendingMemory) -> LongTermMemory | None:
        """Phase 49: an existing confirmed memory of the same kind this
        candidate looks like it may be updating or contradicting. Detection
        only — approving still just adds the new memory; nothing is deleted
        or overwritten automatically. Resolving a real conflict (deleting the
        stale one) is the existing Phase 46 memory-management UI's job."""
        existing = self.store.list(user_id, kind=candidate.kind)
        return find_similar_memory(candidate.content, existing)

    def propose_from_user_message(self, user_id: str, user_message: str) -> list[PendingMemory]:
        proposals: list[PendingMemory] = []
        for candidate in self.extractor.extract(user_message):
            proposal = self.store.propose_candidate(
                user_id,
                candidate.id,
                content=candidate.content,
                kind=candidate.kind,
                rule=candidate.rule,
            )
            if proposal is not None:
                proposals.append(proposal)
        return proposals

    def pending(self, user_id: str) -> list[PendingMemory]:
        return self.store.pending(user_id)

    def approve(self, user_id: str, proposal_id: str) -> LongTermMemory:
        return self.store.approve_candidate(user_id, proposal_id)

    def reject(self, user_id: str, proposal_id: str) -> bool:
        return self.store.reject_candidate(user_id, proposal_id)


__all__ = ["MemoryConfirmationService"]
