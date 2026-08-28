"""Apex AI — an offline-first, model-agnostic RAG assistant.

Package layout (each subpackage owns one responsibility):

- ``config``     centralized settings from environment / `.env`
- ``core``       shared errors, logging, data types
- ``security``   filename + path safety helpers
- ``documents``  extraction (PDF/TXT/MD/JSON), structure-aware chunking, ingestion service
- ``embeddings`` embedding provider abstraction
- ``llm``        LLM provider abstraction (llama.cpp local, Ollama, OpenAI-compatible, transformers)
- ``models``     local model discovery/selection (model manager)
- ``vectordb``   ChromaDB persistence + document registry
- ``retrieval``  hybrid retrieval (vector + BM25) and reranking
- ``rag``        query processing, context building, grounded generation engine
- ``memory``     conversation history/memory (strictly separate from document evidence)
- ``web``        chat-first responsive browser application
- ``ui``         preserved compatibility Gradio interface
- ``api``        FastAPI, streaming chat, conversations, and document uploads
- ``evaluation`` retrieval/answer metrics used by ``evaluate_rag.py``
"""

APP_NAME = "Apex AI"
__version__ = "1.1.0"
