# Apex AI documents & knowledge (phases 41–50)

`apex_documents.py` adds the document boundary between uploads and retrieval:

- `validate_file()` restricts uploads to non-empty PDFs under 100 MB.
- `extract_pages()` preserves one-based page numbers for citations.
- `detect_heading()` identifies numbered and all-caps section headings.
- `smart_chunks()` keeps paragraph boundaries where possible and attaches document, filename, page, chunk index, and heading metadata.
- `document_id()` hashes content, allowing duplicate uploads to be recognized regardless of filename.
- `DocumentQueue` processes documents in a background worker and exposes queued, processing, ready, and failed states.
- `list_documents()` and `delete()` provide the base contract for a document management interface.

The UI can render `Document` records as a table with filename, status, page count, chunk count, upload date, retry, and delete actions. Persistence and Chroma synchronization should be added behind this contract when the web application layer is introduced. Scanned-PDF OCR remains an explicit follow-up because silently indexing empty OCR output would undermine grounded answers.
