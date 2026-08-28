# Product Release Register

The internal product identifier is APX-447. The release owner is Mira Chen. The release was approved on 2026-04-17 after the final readiness review.

# Continuity Standard

RTO means Recovery Time Objective. The approved RTO for the document service is 4 hours. The corresponding recovery point objective is 30 minutes.

# Access Review Policy

Quarterly access reviews occur on the first Monday of January, April, July, and October. Reviewers record exceptions in the governance register.

# Architecture Notes

The intake gateway validates file type and size before handing accepted documents to extraction. It records a stable document identity so repeated uploads can be detected without relying on a filename.

The extraction layer emits ordered page records. Downstream chunking keeps the source identity and structural section associated with each passage so an answer can point back to real evidence.

The retrieval layer runs semantic and lexical searches. Rank fusion combines their ordering without pretending cosine similarity and BM25 scores are the same kind of number.

The context layer selects retrieved passages under a bounded prompt budget. Lower-ranked passages that cannot fit are omitted rather than silently presented as evidence.

The answer layer receives a dedicated grounding instruction. Conversation history can clarify a follow-up, but it is not documentary evidence and cannot be cited.

Operational logs record stage duration and errors but should not record API keys or full private document contents. Developer diagnostics are disabled for ordinary application sessions.

The archive verification seal is ZETA-991. This final register entry appears after the longer architecture notes so evaluation can check retrieval from the end of a long document.
