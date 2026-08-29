# Apex AI Phases 66–67 — Document Collections and Knowledge Base Selection

- **Completed:** 2026-08-29 (America/Chicago)
- **Baseline:** `b389936` (Phase 60, end of Section 5), plus the Phase 61-65/69
  verification pass earlier in this session
  (`docs/PHASE61-65_69_DOCUMENTS_WORKSPACE.md`)
- **Scope:** Phase 66 ("organize documents into collections or knowledge
  bases") and Phase 67 ("a conversation/project uses the appropriate
  knowledge collection") land together — Phase 67 has nothing to select
  without Phase 66 first existing. Both are genuinely new: no prior phase
  built any grouping concept above "one flat per-account document library."
  "Project" from Phase 67's wording is not addressed — there is still no
  project data model (Phase 71, tracked separately since Phase 48/56).

## Design

### Collections are labels; membership lives in the document registry

A `Collection` (`apex_ai/documents/collections.py`, new `CollectionStore`,
its own small SQLite database) holds only an id, an owner, a name, and
timestamps — never document content. Which documents belong to a collection
is tracked in `IngestionService`'s existing JSON registry
(`DocumentInfo.collection_id`), not duplicated into Chroma chunk metadata.
This was a deliberate choice over the alternative (tag every chunk with
`collection_id` the way `user_id` is tagged): moving a document between
collections is common, low-stakes UI interaction, and doing it as a
registry-only update (`IngestionService.move_to_collection`) means it never
touches the vector store, never re-embeds, and is effectively instant —
proven by `test_move_to_collection_is_a_pure_registry_update` asserting the
chunk count is unchanged after a move.

### Retrieval scoping is `document_ids`, not `collection_id`

Rather than teaching the retrieval layer a second, collection-shaped
filter, a collection resolves to a list of document IDs
(`IngestionService.document_ids_for_collection`) before it ever reaches
retrieval. `ChromaVectorStore.search()`, `BM25Index.search()`,
`HybridRetriever.retrieve()`/`retrieve_with_trace()`, and
`RagEngine.ask()`/`ask_stream()`/`prepare()` all gained an optional
`document_ids: list[str] | None` parameter: `None` (the default) searches
the whole account library, unchanged from before this phase; a list
restricts the search to exactly those documents, `[]` restricts it to
none. This keeps the primitive general — it works for "one collection"
today and would work equally well for "one project" once Phase 71 exists,
without retrieval code needing to know what a collection or a project is.

One real bug this surfaced during testing: Chroma's `$in` where-operator
rejects an *empty* list outright (raises `ValueError`, not "no matches").
`ChromaVectorStore.search()` now short-circuits and returns `[]` before
building the query whenever `document_ids` is an empty list —
`test_search_scoped_to_an_empty_collection_returns_nothing` is the
regression test that caught it.

### A conversation is the retrieval-scoping boundary, not a message

`ConversationStore` gained a `collection_id` column (guarded
`ALTER TABLE`, same additive-migration precedent as every prior schema
change since Phase 17). `stream_chat` resolves `document_ids` from
`conversation.collection_id` once per request and passes it straight
through to `engine.ask_stream()` — a conversation scoped to a collection
answers only from that collection for every turn, not just the one that
set it. A conversation can be scoped at creation (`POST /conversations`
or the first `/chat/stream` call that lazily creates one — both paths
validate the collection exists and is owned by the caller) or changed
later (`PATCH /conversations/{id}/collection`).

### `/query` and single-account tools stay unscoped

Consistent with the Phase 54/55 precedent: the singleton engine behind
`/query`, the CLI, Gradio, and the evaluation runner has no per-request
conversation to read a `collection_id` from, so none of them gained
collection scoping. This is the same documented, deliberate exception as
`/query`'s account scoping.

## Frontend

- **Documents page** — a collection filter/management row
  (`#collectionFilterRow`) above the upload zone: "All documents" and
  "Uncategorized" chips plus one per collection, each with inline
  rename/delete actions, and a "+ New collection" chip. Selecting a chip
  filters the document list (`GET /documents?collection_id=`) and becomes
  the target for new uploads from that page (a note under the drop zone
  says so). Each document row gained a small `<select>` to move it between
  collections directly (`PATCH /documents/{id}/collection`) — no page
  reload, no re-ingestion.
- **Chat topbar** — a "Knowledge base" selector next to the conversation
  title, listing "All documents" plus every collection. Changing it on an
  existing conversation calls `PATCH /conversations/{id}/collection`
  immediately; changing it before the first message of a new chat is
  remembered and sent with that first `/chat/stream` call so the
  lazily-created conversation starts pre-scoped.
- Both `.model-picker` selects (model and knowledge base) now share a
  `.topbar-controls` flex wrapper instead of both claiming the topbar
  grid's column 2 directly, which the original single-select CSS grid
  couldn't otherwise accommodate without one overflowing into an
  unplanned row. Verified with `node --check` for JS syntax and the
  existing static-marker test convention (`test_static_assets_include_...`)
  for both files; **not** verified in a live browser — this project's
  frontend has no visual/behavioral test harness (documented already in
  Phase 60), so the usual "static implementation, real-device pass
  recommended before public release" caveat applies here too.

## Files

- `apex_ai/documents/collections.py` (new) — `CollectionStore`.
- `apex_ai/documents/service.py` — `DocumentInfo.collection_id`;
  `ingest_path(..., collection_id="")`; `list_documents(user_id,
  collection_id=None)`; new `move_to_collection()`,
  `unassign_collection()`, `document_ids_for_collection()`; `reindex()`
  now preserves the existing collection assignment instead of resetting it.
- `apex_ai/vectordb/chroma_store.py` — `search()` gained `document_ids`,
  including the empty-list short-circuit fix.
- `apex_ai/retrieval/keyword.py` (`BM25Index`) — `search()` gained
  `document_ids`, filtered against each cached per-account sub-index's own
  metadata.
- `apex_ai/retrieval/pipeline.py` (`HybridRetriever`) — `retrieve()`/
  `retrieve_with_trace()` thread `document_ids` into both channels.
- `apex_ai/rag/engine.py` — `prepare()`/`ask()`/`ask_stream()` gained
  `document_ids`.
- `apex_ai/memory/conversations.py` — `collection_id` column; `create()`
  accepts it; new `set_collection()`.
- `apex_ai/api/collections.py` (new) — `/collections` CRUD router.
- `apex_ai/api/server.py` — `/documents` gained a `collection_id` query
  filter; new `PATCH /documents/{id}/collection`; wires the collections
  router.
- `apex_ai/api/uploads.py` — `/documents/upload` accepts an optional
  `collection_id` form field, validated before ingest.
- `apex_ai/api/chat.py` — `POST /conversations` and `ChatStreamRequest`
  accept `collection_id`; new `PATCH /conversations/{id}/collection`;
  `stream_chat` resolves and passes `document_ids`.
- `apex_ai/config/settings.py`, `.env.example` — `APEX_COLLECTIONS_DB_PATH`.
- `apex_ai/runtime.py` — constructs `services.collections` at startup.
- `apex_ai/web/templates/index.html`, `app.js`, `app.css` — the frontend
  described above.
- `tests/test_collections.py` (new) — store, service-layer, and
  retrieval-layer coverage.
- `tests/test_conversations_web.py` — end-to-end API coverage (collection
  CRUD, upload-into-collection, move, delete-unassigns, conversation
  scoping including the lazy-create path, and the two static-marker
  assertions for the new frontend).
- `tests/test_rag_phase2.py` — updated fake retriever/store doubles to
  accept the new `document_ids` keyword.

## Verification

| Check | Result |
|---|---|
| Full test suite (`python3 -m pytest -q`) | 336 passed, 3 skipped |
| `tests/test_collections.py` | 14 passed |
| `tests/test_conversations_web.py` | 37 passed |
| `node --check apex_ai/web/static/app.js` | passes |
| `ruff check` on every new/touched file | only pre-existing findings (verified unchanged from baseline by diffing rule counts) |

## Deliberately not done in this phase

- **No per-document multi-collection membership.** A document belongs to
  at most one collection (or none), matching a folder mental model rather
  than tags. Multi-collection membership was considered and left out as
  unrequested added complexity; the data model (`collection_id` as a
  single field, not a join table) would need revisiting if a future phase
  actually asks for it.
- **No project integration.** Phase 67's wording mentions "a
  conversation/project" — projects don't exist (Phase 71, still blocked,
  see Phase 56's doc). When Phase 71 lands, it will need its own decision
  about whether a project's documents are its own collection, reference an
  existing one, or something else; nothing here presupposes the answer.
- **No cross-account collection sharing.** Collections are strictly
  per-account, same isolation discipline as everything since Phase 55 —
  not asked for, and would be a much larger design question (shared
  documents raise the same dedup/ownership questions Phase 55 already
  worked through once).
