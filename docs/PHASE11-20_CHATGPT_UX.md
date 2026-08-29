# Apex AI Section 2 — ChatGPT-Style User Experience (Phases 11–20)

- **Completed:** 2026-08-29 (America/Chicago)
- **Baseline:** `33814a7` (Phase 10 developer documentation)
- **Scope:** all ten phases in this roadmap section, audited together because most
  were already substantially built by the "Build chat-first Apex AI web experience"
  work that predates this session (`6e08f92`). One combined doc, per
  `CONTRIBUTING.md`'s guidance that a phase needing no real change still gets a short
  write-up rather than either padding or silent skipping.

## Audit result, phase by phase

| Phase | Ask | Status | Evidence |
|---|---|---|---|
| 11 — Main Chat Layout | Sidebar + main conversation area, not a dashboard | **Already satisfied** | `apex_ai/web/templates/index.html` sidebar/chat-canvas structure; `docs/CHAT_INTERFACE_ARCHITECTURE.md` |
| 12 — New Chat | Real New Chat flow with clean empty-state suggestions | **Already satisfied** | `#newChatButton` → `newChat()`; `buildWelcome()` renders 4 real suggestion chips (Summarize / Compare sources / Verify evidence / Add knowledge), not placeholder text |
| 13 — Message Composer | Multiline box with send/stop/attachment controls | **Already satisfied** | Auto-growing `#messageInput` (`autoResizeComposer`), `#attachButton`, drag-and-drop, `#sendButton` doubles as stop control while generating |
| 14 — Streaming Responses | Real streamed responses + working stop | **Already satisfied** | `POST /chat/stream` NDJSON (Phase 7/8 hardened this), `GenerationManager`, `POST /chat/stop` |
| 15 — Markdown | Headings, lists, links, tables, inline code | **Gap found and closed** — see below | |
| 16 — Code Blocks | Syntax-highlighted code blocks with copy | **Gap found and closed** — see below | |
| 17 — Response Actions | Copy, regenerate, appropriate feedback controls | **Gap found and closed** — see below | |
| 18 — Conversation History | Real saved history, not hardcoded | **Already satisfied** | SQLite `ConversationStore`, exercised by 17 tests in `test_conversations_web.py` |
| 19 — Conversation Management | Rename, delete, open, create | **Already satisfied** | `PATCH/DELETE/GET/POST /conversations[/{id}]` (documented with response schemas in Phase 7) |
| 20 — Responsive Design | Works on desktop/laptop/tablet/mobile | **Already satisfied** | `app.css` breakpoints at 900/720/420px; off-canvas sidebar and bottom-sheet source drawer below 720px |

## What Phase 15 (tables), 16 (highlighting), and 17 (feedback) actually needed

`renderMarkdown()`'s allowlist explicitly stopped at "headings, emphasis, lists,
blockquotes, HTTP(S) links, inline code, and fenced code blocks" (its own prior
documentation said so) — no tables. Code blocks were plain `<pre><code>` with an escaped
text node and a copy button; the language label was displayed but never used to color
anything. Response actions were Copy and Regenerate only — no feedback control existed
at all, in the UI or the backend.

### Phase 15 — Markdown tables

`renderMarkdown()`'s line-by-line pass became index-based (from `for...of` to an
indexed loop) to allow one line of lookahead: a header row immediately followed by a
GFM-style separator row (`| --- | --- |`) opens a table; subsequent `|`-containing
lines become body rows until a blank line, a non-`|` line, or end of input. Output is
wrapped in `<div class="table-wrap">` with `overflow-x: auto` so a wide table scrolls
inside itself on narrow viewports instead of breaking the page layout — the same
pattern the rest of the app already uses at its 900/720/420px breakpoints.

Table cells go through the exact same escape-then-substitute pipeline as every other
line (`text = escapeHTML(text)` happens once, before line-splitting) — verified
directly with a standalone Node test: a `<script>` tag placed inside a table cell
renders as inert escaped text (`&lt;script&gt;...`), never a live element.

### Phase 16 — Syntax-highlighted code blocks

Added `highlightCode(code, lang)`: a small, dependency-free, pattern-based tokenizer —
one alternation regex per recognized language matching comments, strings, numbers, and
a keyword set, each wrapped in a `<span class="tok-*">`. Covers Python, JavaScript/
TypeScript, JSON, YAML, Bash, SQL, Java, C/C++, Go, and Rust. An **unrecognized**
language (or no language) falls back to exactly the prior behavior — plain escaped
text — so nothing regresses for a language this doesn't know.

This is deliberately not a full-grammar highlighter (no CDN library is loaded for the
product UI — see `docs/CHAT_INTERFACE_ARCHITECTURE.md`, an existing architectural
invariant this phase preserves rather than revisits). Comment-pattern choice is
language-scoped on purpose (`#` for Python/Bash/YAML, `//`/`/* */` for C-style
languages, `--` for SQL) rather than one universal pattern, specifically to avoid a
real false-positive class: a universal `#` pattern would miscolor a CSS hex color or a
JS private field (`#foo`) as a comment. The copy button reads `element.textContent`,
which ignores the highlighter's `<span>` wrappers, so copied code is still exactly the
original plain text — verified by the existing `navigator.clipboard` test coverage
plus manual reading of that code path (it was unchanged by this phase).

### Phase 17 — Response feedback

Added a per-message up/down reaction next to Copy/Regenerate. This is genuinely local:
`messages.feedback` (nullable `'up'|'down'`, SQLite `CHECK` constraint) added to the
existing `conversations.db` schema via a guarded `ALTER TABLE` (checked against
`PRAGMA table_info` first, since SQLite has no portable `ADD COLUMN IF NOT EXISTS`) —
so existing databases with real conversation history upgrade in place instead of
requiring a rebuild. `POST /conversations/{id}/messages/{message_id}/feedback` sets or
clears it (clicking an already-active thumb clears it) and only matches `role='assistant'`
rows, so it 404s on a user message or a wrong conversation/message pairing. Nothing
reads this value for generation, retrieval, or anything cross-user — there is no
"anything cross-user" in a single-user local app, and the roadmap's later evaluation
phases (38–40) are the natural place to ever use this signal, not this phase.

## Deliberately not changed

- No feedback *reason*/comment field — "appropriate feedback controls" for a
  single-user local app with no aggregation backend reasonably means "the user can
  flag a response," not a structured survey.
- No table alignment (`:---`, `---:`, `:---:`) parsing — cells always render
  left-aligned. GFM alignment syntax is accepted (treated the same as an unaligned
  separator) rather than rejected, so a pasted GFM table with alignment markers still
  renders as a table; it just doesn't honor the alignment yet.
- No highlighting for HTML/CSS/XML — a generic keyword/string/comment/number
  tokenizer is a poor fit for markup (tag/attribute structure, not keywords), and a
  wrong attempt would misfire more visibly than no highlighting; left as plain text
  rather than guessed at.
- No new CDN dependency for a "real" highlighter (e.g. highlight.js/Prism) — would
  contradict this app's own established no-CDN-for-product-UI architecture (see
  `docs/PHASE2_ARCHITECTURE_MAP.md` section 5) for a feature a ~100-line function
  already covers adequately for the common cases.

## Verification

| Check | Result |
|---|---|
| Full test suite (`python -m pytest tests/ -q`) | 232 passed, 3 skipped |
| New backend test: feedback set/toggle/clear, invalid value → 422, missing message → 404, feedback on a user message → 404 | Included in the 232 |
| `node --check apex_ai/web/static/app.js` | Passes |
| Standalone Node harness exercising the real `renderMarkdown`/`highlightCode` source (tables, code highlighting, lists, headings, bold) | All assertions passed |
| XSS regression check: `<script>` inside a table cell | Rendered as escaped text, not a live tag |
| `ruff check` on touched Python files | All checks passed |

## Boundaries and remaining unknowns

- The highlighter and table parser are exercised by a standalone Node script during
  development and by Python string-presence assertions in
  `test_static_assets_include_responsive_themes_and_code_blocks`; there is still no
  in-repo JS test runner, so a future change to `renderMarkdown()` could regress
  silently if it isn't re-verified the same way.
- No real-browser/visual verification of table/code-block rendering in this session
  (no browser available here) — logic was verified against real Node execution of the
  actual source, not by inspection alone, but a manual visual pass is still worth doing
  before this ships to an actual user.
- Per-message feedback has no UI surface for reviewing "all my down-voted answers" yet
  — it is stored and retrievable via `GET /conversations/{id}`, but nothing in the
  Documents/Settings pages aggregates it. That would be new scope, not part of this
  phase's "appropriate feedback controls" ask.
