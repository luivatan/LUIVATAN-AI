# Apex AI memory & advanced AI (phases 71–80)

`apex_memory.py` separates conversational memory from document/RAG context. Memory is user-scoped SQLite data and can be searched or deleted independently of indexed documents.

Capabilities include bounded recent history, conversation search, per-user deletion, JSON preferences, multiple model labels, side-by-side model comparison formatting, and persistent latency/token/success metrics for an analytics dashboard. `trim_context()` applies a character budget before history enters a prompt.

The future dashboard can render `analytics()` as calls, average latency, success rate, and token usage by model. Metrics should be redacted of prompt contents and access-controlled like all account data. Model comparison should use the same prompt and retrieved context for a fair evaluation.
