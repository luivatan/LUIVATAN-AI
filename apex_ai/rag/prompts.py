"""Prompt construction for grounded generation.

The system prompt encodes the anti-hallucination contract (spec #15):

- answer ONLY from retrieved evidence,
- never invent facts or pretend a source says something it doesn't,
- distinguish evidence from inference,
- say when evidence is insufficient,
- cite with [n] markers matching the numbered context blocks.

Conversation memory and confirmed long-term memory (Phase 47) are each included in
their own clearly separated section with an explicit instruction that neither is
evidence. Medical mode adds a safety preamble (informational, not advice, no
diagnosis).
"""

from __future__ import annotations

from apex_ai.memory.context import build_conversation_context

SYSTEM_GROUNDED = """You are Apex AI, a careful document assistant.

Rules:
1. Answer using ONLY the retrieved evidence below. Do not add outside facts from memory.
2. Do not invent facts, source names, page numbers, quotations, or capabilities.
3. Cite a claim with [1] or [2] only when that numbered evidence block directly supports it.
4. Address each distinct part of a multi-part question. If evidence supports only some parts, answer those and say which other parts are not covered.
5. If no evidence supports the answer, say so plainly instead of guessing. A concise synthesis is allowed only when it follows directly from cited evidence; label uncertainty.
6. Conversation history helps interpret follow-ups. It is NOT evidence and must never be cited.
7. Never mention or cite a source that is absent from the retrieved evidence blocks.
8. User context (if present) is the user's own stated preferences/situation. Use it to shape tone and relevance, never as a factual source, and never cite it.

Evidence blocks follow the format:
[n]
SOURCE: document name
PAGE: page number
SECTION: section title
<relevant text>"""

MEDICAL_ADDENDUM = """
Medical context: This system provides information extracted from documents. It is NOT medical
advice, NOT a diagnosis, and NOT a substitute for a qualified professional. When documents
describe treatments or doses, report them as what the document states, include uncertainty,
and recommend consulting a healthcare professional for personal decisions."""


def format_history(
    history: list[dict] | None,
    max_turns: int = 3,
    *,
    char_limit: int = 2400,
    message_char_limit: int = 1000,
) -> str:
    """Compatibility wrapper around the bounded conversation-context builder."""
    context = build_conversation_context(
        history,
        max_turns=max_turns,
        char_limit=char_limit,
        message_char_limit=message_char_limit,
    )
    return context.text or "(no previous conversation)"


def build_messages(
    question: str,
    evidence_context: str,
    history: list[dict] | None = None,
    medical: bool = False,
    system_prompt: str | None = None,
    *,
    history_text: str | None = None,
    memory_text: str | None = None,
) -> list[dict]:
    """Build the chat-messages payload for the LLM.

    Input: the user's original question, the formatted evidence block,
    conversation history (memory — not evidence), and the medical flag.
    ``history_text`` accepts the exact prebuilt bounded context so the engine
    does not independently format or budget history twice. ``memory_text``
    (Phase 47) is the relevance-filtered confirmed-memory block, or omitted
    entirely when empty — it never displaces or gets confused with document
    evidence. Output: list of {"role", "content"} dicts for chat-template
    providers.
    """
    system = system_prompt or SYSTEM_GROUNDED
    if medical:
        system += MEDICAL_ADDENDUM

    rendered_history = history_text if history_text is not None else format_history(history)
    rendered_history = rendered_history or "(no previous conversation)"
    memory_block = (
        f"User context (preferences/situation, not evidence, never cite):\n{memory_text}\n\n"
        if memory_text
        else ""
    )
    user_content = (
        f"{memory_block}"
        "Conversation history (context only, not evidence):\n"
        f"{rendered_history}\n\n"
        f"Retrieved evidence:\n{evidence_context}\n\n"
        f"Question: {question}\n\n"
        "Answer (cite evidence with [n] markers; state plainly if evidence is insufficient):"
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user_content},
    ]


# Returned verbatim (without an LLM call) when retrieval confidence is too low.
INSUFFICIENT_EVIDENCE_ANSWER = (
    "I couldn't find enough information in the provided documents to answer this question.\n\n"
    "You can try: rephrasing the question with different words, uploading a document that "
    "covers the topic, or checking which documents are indexed in the Documents tab."
)
