# Apex AI RAG & vector search (phases 51–60)

`apex_retrieval.py` separates retrieval from the UI and generation layers.

- `EmbeddingSystem` lazily loads SentenceTransformers and batches text encoding.
- `ChromaStore` provides persistent ChromaDB storage with metadata-preserving upserts and semantic candidate search.
- `keyword_search()` supplies lexical matching for exact terms that embeddings can miss.
- `hybrid_retrieve()` fuses ranked semantic and keyword candidates with reciprocal-rank weighting and deduplication.
- `optimize_context()` produces bounded, citation-ready context without cutting a chunk mid-text.

The result score is a ranking signal, not a medical confidence score. A later reranker can be inserted between candidate retrieval and hybrid fusion (for example, a cross-encoder), with a configurable minimum relevance threshold before generation. The existing Chroma collection remains compatible with this layer.
