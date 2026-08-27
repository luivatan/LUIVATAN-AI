# Apex AI intelligent answers (phases 61–70)

`apex_answers.py` adds the grounded-answer layer above retrieval:

- Query rewriting handles context-dependent follow-ups without adding facts.
- Question decomposition splits compound questions for independent retrieval.
- Context building preserves source/page metadata and enforces a context budget.
- Evidence detection extracts citations explicitly present in the response.
- Grounded prompts instruct the model to refuse unsupported claims rather than guess.
- Citation generation supports numbered page/source references.
- `source_viewer()` provides the selected evidence text for the UI.
- `evaluate_answer()` reports citation coverage and evidence presence as initial RAG evaluation signals.

Evaluation metrics are diagnostic, not a measure of medical correctness. Production evaluation should add a versioned golden dataset, human review, retrieval recall, faithfulness grading, and regression checks for refusal behavior.
