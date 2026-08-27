"""Prompt construction for grounded generation.

The system prompt encodes the anti-hallucination contract (spec #15):

- answer ONLY from retrieved evidence,
- never invent facts or pretend a source says something it doesn't,
- distinguish evidence from inference,
- say when evidence is insufficient,
- cite with [n] markers matching the numbered context blocks.

Conversation memory is included in a clearly separated section with an
explicit instruction that it is context, not evidence. Medical mode adds a
safety preamble (informational, not advice, no diagnosis).
"""

from __future__ import annotations

SYSTEM_GROUNDED = """You are Apex AI, a careful document assistant.

Rules:
1. Answer using ONLY the retrieved evidence below.
2. Do not invent facts. Do not pretend a source says something it does not say.
3. Cite evidence with bracketed markers like [1] or [2] that match the numbered evidence blocks.
4. If the evidence is insufficient, say so plainly instead of guessing.
5. You may add clearly-labeled general reasoning, but distinguish it from what the evidence says.
6. The conversation history is context for understanding the question. It is NOT evidence and must not be cited.

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


def format_history(history: list[dict] | None, max_turns: int = 3) -> str:
    if not history:
        return "(no previous conversation)"
    recent = history[-max_turns:]
    return "\n".join(
        f"User: {turn.get('user', '')}\nAssistant: {str(turn.get('assistant', ''))[:400]}"
        for turn in recent
    )


def build_messages(
    question: str,
    evidence_context: str,
    history: list[dict] | None = None,
    medical: bool = False,
    system_prompt: str | None = None,
) -> list[dict]:
    """Build the chat-messages payload for the LLM.

    Input: the user's original question, the formatted evidence block,
    conversation history (memory — not evidence), and the medical flag.
    Output: list of {"role", "content"} dicts for chat-template providers.
    """
    system = system_prompt or SYSTEM_GROUNDED
    if medical:
        system += MEDICAL_ADDENDUM

    user_content = (
        f"Conversation history (context only, not evidence):\n{format_history(history)}\n\n"
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
