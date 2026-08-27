"""Apex AI — Gradio user interface.

The workflow mirrors the product spec:

    open Apex AI -> select model -> upload documents -> wait for processing
      -> ask question -> receive answer -> view sources

Layout: a status header (model, document/chunk counts, embedding model) plus
four tabs — Chat, Documents, Models, About. Every subsystem error surfaces as
a readable "WHAT / WHY / HOW TO FIX" message, never a raw traceback.
"""

from __future__ import annotations

import gradio as gr

from apex_ai import APP_NAME, __version__
from apex_ai.core.errors import ApexError
from apex_ai.core.logging import get_logger
from apex_ai.models.manager import ModelManager
from apex_ai.runtime import ApexServices, build_services

log = get_logger("ui")

_MEDICAL_NOTICE = (
    "**Medical use disclaimer:** Apex AI provides information extracted from your documents. "
    "It is **not** medical advice, not a diagnosis, and not a substitute for a qualified "
    "healthcare professional. Always verify citations and consult a professional for "
    "personal health decisions."
)


def _services_banner(services: ApexServices) -> str:
    if not services.ready:
        return f"⚠️ **Startup problem**\n\n{services.startup_error}"
    docs = len(services.ingestion.list_documents())
    chunks = services.store.count()
    model = services.settings.model_path or "(no GGUF selected — choose one in Models)"
    embed = services.embeddings.name
    return (
        f"**Model:** `{model}`  |  **Embeddings:** `{embed}`  |  "
        f"**Documents:** {docs} ({chunks} chunks)  |  **Provider:** `{services.settings.llm_provider}`"
    )


def create_app(services: ApexServices | None = None) -> gr.Blocks:
    """Build the Blocks interface. Safe to call even when startup failed —
    the banner explains what to fix."""
    services = services or build_services()

    with gr.Blocks(title=APP_NAME) as interface:
        gr.Markdown(f"# 🚀 {APP_NAME}")
        gr.Markdown(
            f"Offline-first, source-grounded document assistant · v{__version__}"
        )
        banner = gr.Markdown(_services_banner(services))

        with gr.Tabs():
            # ---------------- CHAT ----------------
            with gr.Tab("Chat"):
                question = gr.Textbox(
                    label="Question",
                    placeholder="Ask a question about your documents…",
                    lines=2,
                )
                with gr.Row():
                    ask_button = gr.Button("Ask", variant="primary")
                    clear_button = gr.Button("Clear conversation")
                answer = gr.Markdown()
                with gr.Row():
                    with gr.Column(scale=1):
                        sources = gr.Dropdown(
                            label="Sources (from this answer)", choices=[], interactive=True
                        )
                        source_viewer = gr.Textbox(
                            label="Source text",
                            lines=12,
                            value="Retrieved evidence for the selected citation appears here.",
                            interactive=False,
                        )
                    with gr.Column(scale=1):
                        history_box = gr.Textbox(
                            label="Conversation history",
                            lines=12,
                            value=(services.memory.display() if services.memory else ""),
                            interactive=False,
                            info="Memory helps the model understand follow-ups. It is never "
                                 "used as document evidence.",
                        )
                gr.Markdown(_MEDICAL_NOTICE)

            # ---------------- DOCUMENTS ----------------
            with gr.Tab("Documents"):
                upload = gr.File(
                    label="Upload PDF / TXT / Markdown / JSON",
                    file_types=[".pdf", ".txt", ".md", ".markdown", ".json"],
                    file_count="multiple",
                    type="filepath",
                )
                ingest_button = gr.Button("Process uploads", variant="primary")
                ingest_status = gr.Markdown()
                library = gr.Dataframe(
                    headers=["name", "type", "pages", "chunks", "added", "medical"],
                    label="Indexed documents",
                    interactive=False,
                    value=_library_rows(services),
                )
                with gr.Row():
                    reindex_choice = gr.Dropdown(
                        label="Document", choices=_document_choices(services), interactive=True
                    )
                    reindex_button = gr.Button("Re-index")
                    delete_button = gr.Button("Delete from index", variant="stop")
                manage_status = gr.Markdown()

            # ---------------- MODELS ----------------
            with gr.Tab("Models"):
                gr.Markdown(
                    "Local GGUF models are discovered in "
                    f"`{services.settings.model_dir}` (configurable via `APEX_MODEL_DIR`)."
                )
                models_table = gr.Dataframe(
                    headers=["name", "type", "size", "provider", "status", "active"],
                    label="Detected models",
                    interactive=False,
                    value=_model_rows(services),
                )
                with gr.Row():
                    model_choice = gr.Dropdown(
                        label="Select model", choices=_model_choices(services), interactive=True
                    )
                    select_button = gr.Button("Select & validate")
                    refresh_button = gr.Button("Refresh")
                model_status = gr.Markdown()

            # ---------------- ABOUT ----------------
            with gr.Tab("About"):
                gr.Markdown(
                    f"## {APP_NAME}\n\n"
                    "An offline-first, model-agnostic RAG assistant.\n\n"
                    "**How an answer is produced:** your question is embedded and matched "
                    "against your documents (hybrid vector + keyword retrieval), the best "
                    "evidence is reranked and placed in a context with SOURCE / PAGE / "
                    "SECTION headers, and the local LLM must answer only from that evidence. "
                    "Citations are built only from chunks actually sent to the model.\n\n"
                    "**Privacy:** documents, models, and conversation memory stay on this "
                    "machine. No internet is required after models are downloaded.\n\n"
                    + _MEDICAL_NOTICE
                )

        # ---------------- callbacks ----------------

        def do_ask(text: str):
            if not services.ready:
                yield f"⚠️ **Apex AI cannot start yet**\n\n{services.startup_error}", gr.update(), ""
                return
            if not text or not text.strip():
                yield "Please type a question first.", gr.update(), services.memory.display()
                return

            parts: list[str] = []
            try:
                for event in services.engine.ask_stream(text.strip()):
                    if event["type"] == "token":
                        parts.append(event["text"])
                        yield "\n".join(parts), gr.update(), services.memory.display()
                    else:
                        result = event["result"]
                        final_text = result.answer
                        if result.sources_block:
                            final_text += f"\n\n{result.sources_block}"
                        services._extras["last_citations"] = {
                            c.label(): c.text for c in result.citations
                        }
                        choices = [c.label() for c in result.citations]
                        yield final_text, gr.update(
                            choices=choices, value=choices[0] if choices else None
                        ), services.memory.display()
            except ApexError as error:
                yield error.user_message(), gr.update(), services.memory.display()
            except Exception as error:  # unexpected
                log.exception("Chat failed")
                yield (
                    f"Chat failed unexpectedly.\n\nDetails: {type(error).__name__}: {error}\n"
                    "Technical details were written to logs/apex.log."
                ), gr.update(), services.memory.display()

        def show_source(label: str):
            if not label:
                return "Select a citation to view its retrieved text."
            citations = services._extras.get("last_citations", {})
            if not citations:
                return "Source text is not available for this answer."
            return citations.get(label, "Source text is not available for this answer.")

        def do_ingest(files, progress=gr.Progress()):
            if not services.ready:
                return _startup_blocked(services)
            if not files:
                return "Select one or more files first."
            lines = []
            for file_path in files:
                try:
                    result = services.ingestion.ingest_path(file_path)
                    lines.append(f"- **{result.message}**")
                except ApexError as error:
                    lines.append(f"- ❌ {error.user_message()}")
                except Exception as error:
                    log.exception("Ingest failed")
                    lines.append(f"- ❌ Unexpected error on `{file_path}`: {error}")
            refresh = _library_rows(services)
            return "\n".join(lines)  # noqa: RET504  (library refreshed via separate output)

        def do_reindex(name):
            if not name:
                return "Choose a document first."
            try:
                info = next(
                    (d for d in services.ingestion.list_documents() if d.name == name), None
                )
                if not info:
                    return f"Document '{name}' not found."
                result = services.ingestion.reindex(info.document_id)
                return result.message
            except ApexError as error:
                return error.user_message()

        def do_delete(name):
            if not name:
                return "Choose a document first."
            try:
                info = next(
                    (d for d in services.ingestion.list_documents() if d.name == name), None
                )
                if not info:
                    return f"Document '{name}' not found."
                return services.ingestion.remove(info.document_id)
            except ApexError as error:
                return error.user_message()

        def do_select_model(name):
            if not name:
                return "Choose a model first."
            try:
                path = services.select_model(name)
                info = services.active_llm().get_model_info()
                return (
                    f"✅ Model selected: `{path}`\n\n{info.summary()}\n\n"
                    "It will load automatically when you ask the first question."
                )
            except ApexError as error:
                return error.user_message()

        def do_refresh_models():
            return (
                _model_rows(services),
                gr.update(choices=_model_choices(services)),
                _services_banner(services),
            )

        def do_clear_memory():
            if services.memory:
                services.memory.clear()
            return services.memory.display()

        # ---- wiring ----
        ask_button.click(
            do_ask, inputs=question, outputs=[answer, sources, history_box]
        )
        question.submit(
            do_ask, inputs=question, outputs=[answer, sources, history_box]
        )
        clear_button.click(do_clear_memory, inputs=None, outputs=history_box)

        ingest_button.click(
            fn=lambda files: (do_ingest(files), _library_rows(services), gr.update(
                choices=_document_choices(services))),
            inputs=upload,
            outputs=[ingest_status, library, reindex_choice],
        )
        reindex_button.click(
            fn=lambda name: (do_reindex(name), _library_rows(services)),
            inputs=reindex_choice,
            outputs=[manage_status, library],
        )
        delete_button.click(
            fn=lambda name: (do_delete(name), _library_rows(services), gr.update(
                choices=_document_choices(services))),
            inputs=reindex_choice,
            outputs=[manage_status, library, reindex_choice],
        )

        select_button.click(do_select_model, inputs=model_choice, outputs=model_status)
        refresh_button.click(
            do_refresh_models,
            inputs=None,
            outputs=[models_table, model_choice, banner],
        )
        sources.change(show_source, inputs=sources, outputs=source_viewer)

    return interface


# -- helpers ------------------------------------------------------------------

def _startup_blocked(services: ApexServices) -> str:
    return f"⚠️ **Apex AI cannot start yet**\n\n{services.startup_error}"


def _library_rows(services: ApexServices):
    if not services.ingestion:
        return []
    return [
        [d.name, d.file_type, d.pages, d.chunks, d.created_at, "yes" if d.looks_medical else "no"]
        for d in services.ingestion.list_documents()
    ]


def _document_choices(services: ApexServices):
    if not services.ingestion:
        return []
    return [d.name for d in services.ingestion.list_documents()]


def _model_rows(services: ApexServices):
    return [entry.row() for entry in ModelManager(services.settings).discover()]


def _model_choices(services: ApexServices):
    return [entry.name for entry in ModelManager(services.settings).discover()]


def launch(services: ApexServices | None = None, **kwargs) -> None:
    """Entry point used by ui.py / ingest.py / launch script."""
    services = services or build_services()
    interface = create_app(services)
    interface.launch(
        server_name=services.settings.server_name,
        server_port=services.settings.server_port,
        **kwargs,
    )
