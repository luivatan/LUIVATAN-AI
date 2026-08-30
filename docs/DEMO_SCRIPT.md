# Apex AI Demo Script (Phase 97)

A ~6 minute walkthrough: **chat → upload document → ask question →
grounded answer → sources.** Every question below is a real, already
-verified fixture from `eval/dataset.example.jsonl` (Phase 2's evaluation
harness) against `eval/docs/` — nothing here is scripted or faked; if the
model gives a different wording, that's real model output, not a
rehearsed line.

## Before the demo

1. Make sure Apex AI is running with a real model configured (see
   `README.md`'s "Model setup" section) — this demo is meant to show the
   real product, not a stand-in.
2. Run the seed script once:

   ```
   python scripts/seed_demo.py
   ```

   This creates a **"Demo: Apex Research"** collection and ingests three
   of the four demo documents (`sample_first_aid.pdf`,
   `apex_operations.md`, `apex_finance.md`). It's idempotent — safe to
   run again before every demo; already-ingested documents are skipped
   as duplicates rather than re-indexed.
3. `eval/docs/burn_care.md` is deliberately **not** pre-seeded — you
   upload it live in step 2 below, so the demo shows a real upload, not
   just pre-loaded documents.

## Walkthrough

### 1. Start a new chat

Open Apex AI, click **New chat**. Point out the empty state's real
copy: "Ask questions across your private documents. Apex AI retrieves
the evidence, answers with your local model, and shows exactly where it
came from." — that promise is what the rest of the demo proves.

### 2. Upload a document live

Click **Add knowledge** (or go to **Documents** → **Upload documents**)
and upload `eval/docs/burn_care.md`. Narrate what's happening while it
indexes: the file is hashed for duplicate detection, extracted, chunked,
embedded, and stored — the same real pipeline as any document a user
uploads, just small enough to finish in a couple of seconds.

### 3. Ask a question answered by the document you just uploaded

> **Ask:** "How long should a burn be cooled with running water?"

**Expected grounded answer:** *20 minutes.* Point out the citation chip
under the answer — click it to open the source viewer and show the
literal sentence in `burn_care.md` the answer came from.

### 4. Scope to the pre-seeded collection and ask a direct question

Use the knowledge-base picker above the composer (defaults to "All
documents") to select **Demo: Apex Research**. This shows collection
-scoped retrieval — only documents in that collection are searched.

> **Ask:** "What temperature counts as a fever in adults?"

**Expected grounded answer:** *38°C or higher*, cited to
`sample_first_aid.pdf`, page 1.

### 5. Ask an exact-match question (numbers/IDs, not just prose)

> **Ask:** "What is the internal product identifier APX-447, and on
> what exact date was it approved?"

**Expected grounded answer:** identifier *APX-447*, approved
*2026-04-17*, cited to `apex_operations.md`. This demonstrates hybrid
retrieval's keyword channel — exact identifiers and dates are retained
as whole tokens, not just embedded as prose meaning.

### 6. Ask a multi-document question

> **Ask:** "What is the APX-447 release owner, and what was Apex
> Research's recurring revenue in 2025?"

**Expected grounded answer:** *Mira Chen*, *$12.4 million* — cited to
**both** `apex_operations.md` and `apex_finance.md`. This shows Apex AI
fusing evidence from more than one source into a single answer, each
half separately attributed.

### 7. Ask a question the documents can't answer

> **Ask:** "What is the lunar greenhouse oxygen quota?"

**Expected behavior:** Apex AI declines to answer from the retrieved
evidence instead of guessing — this is the conservative
semantic-plus-lexical evidence gate (see `README.md`'s "Grounded
generation" feature) refusing when retrieved context doesn't actually
support an answer. This is the single most important moment in the
demo: point out that a generic chatbot would likely hallucinate a
plausible-sounding number here instead.

## What this demo deliberately does not claim

- The exact wording of the model's answer will vary slightly by
  configured model (llama.cpp vs. Ollama vs. OpenAI-compatible) — the
  **facts and citations** are what's being demonstrated, not an exact
  transcript.
- This is a real product walkthrough, not a scripted video with staged
  screenshots — see `docs/PHASE96_LANDING_PAGE.md` and
  `docs/PHASE97_DEMO_EXPERIENCE.md` for what else this phase covers.
