"""User-confirmation workflow for safe long-term-memory candidates.

Pending proposals are not model context and are not confirmed memories. Only an
explicit approve operation moves one into the long-term-memory table.
"""

from __future__ import annotations

from apex_ai.memory.extraction import MemoryCandidateExtractor
from apex_ai.memory.long_term import LongTermMemory, LongTermMemoryStore, PendingMemory


class MemoryConfirmationService:
    def __init__(
        self,
        extractor: MemoryCandidateExtractor,
        store: LongTermMemoryStore,
    ) -> None:
        self.extractor = extractor
        self.store = store

    def propose_from_user_message(self, user_message: str) -> list[PendingMemory]:
        proposals: list[PendingMemory] = []
        for candidate in self.extractor.extract(user_message):
            proposal = self.store.propose_candidate(
                candidate.id,
                content=candidate.content,
                kind=candidate.kind,
                rule=candidate.rule,
            )
            if proposal is not None:
                proposals.append(proposal)
        return proposals

    def pending(self) -> list[PendingMemory]:
        return self.store.pending()

    def approve(self, proposal_id: str) -> LongTermMemory:
        return self.store.approve_candidate(proposal_id)

    def reject(self, proposal_id: str) -> bool:
        return self.store.reject_candidate(proposal_id)


__all__ = ["MemoryConfirmationService"]
