import hashlib
import json
import shutil
import traceback
from pathlib import Path

import chromadb
import gradio as gr
from llama_cpp import Llama
from sentence_transformers import SentenceTransformer

DATABASE_PATH = "./database"
COLLECTION_NAME = "medical_docs"
MEMORY_PATH = Path("conversation_memory.json")
UPLOAD_DIR = Path("uploaded_pdfs")
CHUNK_SIZE = 1000
CHUNK_OVERLAP = 150
MEMORY_LIMIT = 8
MEDICAL_WARNING = (
    "This AI is designed for medical documents. "
    "Results may be less reliable for non-medical content."
)
MEDICAL_KEYWORDS = {
    "anatomy",
    "antibiotic",
    "blood",
    "cardiac",
    "care",
    "cell",
    "clinical",
    "diagnosis",
    "disease",
    "doctor",
    "dose",
    "drug",
    "health",
    "hospital",
    "infection",
    "injury",
    "lab",
    "medical",
    "medicine",
    "nurse",
    "patient",
    "pharmacology",
    "physician",
    "prescription",
    "symptom",
    "therapy",
    "treatment",
    "vaccine",
}

# =========================
# LOAD EMBEDDING MODEL
# =========================

embed_model = SentenceTransformer("all-MiniLM-L6-v2")

# =========================
# LOAD LOCAL LLM
# =========================

llm = Llama(
    model_path="/media/shaggvt/progames/lm/qwen2.5-coder-7b-instruct-q4_k_m.gguf",
    n_ctx=2048,
)

# =========================
# LOAD DATABASE
# =========================

client = chromadb.PersistentClient(path=DATABASE_PATH)

collection = client.get_or_create_collection(
    name=COLLECTION_NAME
)


# =========================
# PERSISTENT MEMORY
# =========================

def load_memory():
    if not MEMORY_PATH.exists():
        return []

    try:
        with MEMORY_PATH.open("r", encoding="utf-8") as memory_file:
            memory = json.load(memory_file)
    except (json.JSONDecodeError, OSError):
        return []

    if not isinstance(memory, list):
        return []

    return memory


conversation_memory = load_memory()


def save_memory():
    with MEMORY_PATH.open("w", encoding="utf-8") as memory_file:
        json.dump(conversation_memory, memory_file, indent=2)


def display_conversation():
    return "\n\n".join(
        f"User: {item['user']}\nAssistant: {item['assistant']}"
        for item in conversation_memory
    )


def format_conversation_memory(limit=MEMORY_LIMIT):
    recent_messages = conversation_memory[-limit:]

    if not recent_messages:
        return "No previous conversation."

    return "\n".join(
        f"User: {item['user']}\nAssistant: {item['assistant']}"
        for item in recent_messages
    )


# =========================
# PDF INDEXING
# =========================

def safe_filename(filename):
    return Path(filename).name.replace("/", "_").replace("\\", "_")


def file_sha256(path):
    digest = hashlib.sha256()

    with open(path, "rb") as source_file:
        for block in iter(lambda: source_file.read(1024 * 1024), b""):
            digest.update(block)

    return digest.hexdigest()


def extract_pdf_pages(pdf_path):
    try:
        from pypdf import PdfReader
    except ImportError as error:
        raise RuntimeError(
            "Missing dependency: install pypdf to extract uploaded PDFs."
        ) from error

    reader = PdfReader(pdf_path)
    pages = []

    for page_index, page in enumerate(reader.pages, start=1):
        text = page.extract_text() or ""

        if text.strip():
            pages.append((page_index, text.strip()))

    if not pages:
        raise RuntimeError(
            "No readable text was found in this PDF. If it is a scanned PDF, run OCR first."
        )

    return pages


def chunk_text(text, chunk_size=CHUNK_SIZE, overlap=CHUNK_OVERLAP):
    cleaned = " ".join(text.split())

    if not cleaned:
        return []

    chunks = []
    start = 0

    while start < len(cleaned):
        end = start + chunk_size
        chunk = cleaned[start:end].strip()

        if chunk:
            chunks.append(chunk)

        if end >= len(cleaned):
            break

        start = max(end - overlap, start + 1)

    return chunks


def is_likely_medical_document(pages):
    text = " ".join(page_text.lower() for _, page_text in pages)
    matches = sum(1 for keyword in MEDICAL_KEYWORDS if keyword in text)

    return matches >= 3


def index_uploaded_pdf(pdf_path):
    if not pdf_path:
        return "Upload a PDF first."

    try:
        UPLOAD_DIR.mkdir(exist_ok=True)

        source_path = Path(pdf_path)
        filename = safe_filename(source_path.name)
        saved_path = UPLOAD_DIR / filename
        shutil.copy2(source_path, saved_path)

        source_hash = file_sha256(saved_path)
        pages = extract_pdf_pages(saved_path)
        is_medical_document = is_likely_medical_document(pages)

        ids = []
        documents = []
        embeddings = []
        metadatas = []

        for page_number, page_text in pages:
            for chunk_index, chunk in enumerate(chunk_text(page_text), start=1):
                doc_id = f"{source_hash}:p{page_number}:c{chunk_index}"

                ids.append(doc_id)
                documents.append(chunk)
                embeddings.append(embed_model.encode(chunk).tolist())
                metadatas.append(
                    {
                        "source": filename,
                        "source_hash": source_hash,
                        "page": page_number,
                        "chunk": chunk_index,
                        "is_likely_medical": is_medical_document,
                    }
                )

        if not documents:
            return f"No indexable text chunks were found in {filename}."

        collection.upsert(
            ids=ids,
            documents=documents,
            embeddings=embeddings,
            metadatas=metadatas,
        )

        status = f"Indexed {len(documents)} chunks from {filename}."

        if not is_medical_document:
            status = f"{status}\n\nWarning: {MEDICAL_WARNING}"

        return status
    except Exception as error:
        traceback.print_exc()
        return f"Indexing failed: {error}"


# =========================
# RETRIEVAL AND CHAT
# =========================

def citation_label(metadata):
    source = metadata.get("source", "unknown source")
    page = metadata.get("page")
    chunk = metadata.get("chunk")

    parts = [source]

    if page is not None:
        parts.append(f"page {page}")

    if chunk is not None:
        parts.append(f"chunk {chunk}")

    return ", ".join(parts)


def format_retrieved_context(results):
    documents = results.get("documents", [[]])[0]
    metadatas = results.get("metadatas", [[]])[0]
    ids = results.get("ids", [[]])[0]
    distances = results.get("distances", [[]])[0]

    context_blocks = []
    citations = []

    print("\n====================")
    print("RETRIEVED CHUNKS")
    print("====================\n")

    for index, document in enumerate(documents, start=1):
        metadata = metadatas[index - 1] if index - 1 < len(metadatas) else {}
        doc_id = ids[index - 1] if index - 1 < len(ids) else "unknown"
        distance = distances[index - 1] if index - 1 < len(distances) else None
        citation = citation_label(metadata)

        print(f"[{index}] {citation} | ID: {doc_id} | Distance: {distance}")
        print(document[:1000])
        print()

        context_blocks.append(
            f"[{index}] {citation}\n{document}"
        )
        citations.append(f"[{index}] {citation}")

    return "\n\n".join(context_blocks), citations


def ask_ai(question):
    if not question or not question.strip():
        return "Ask a question first.", display_conversation()

    try:
        query_embedding = embed_model.encode(question).tolist()

        results = collection.query(
            query_embeddings=[query_embedding],
            n_results=5,
            include=["documents", "metadatas", "distances"],
        )

        retrieved_context, citations = format_retrieved_context(results)

        if not retrieved_context:
            return (
                "I could not find indexed document chunks. Upload and index a PDF first.",
                display_conversation(),
            )

        memory_context = format_conversation_memory()

        prompt = f"""
You are an advanced medical AI assistant.

Answer the question using ONLY the retrieved medical information.
Use the conversation memory only to understand context from the user's current question.
Do not invent facts from memory.
Include bracketed source citations like [1] for claims that come from retrieved information.
If the answer is unclear, explain what information was found.

Conversation Memory:
{memory_context}

Retrieved Medical Information:
{retrieved_context}

Question:
{question}

Detailed Answer:
"""

        output = llm(
            prompt,
            max_tokens=500,
            temperature=0.2,
        )

        answer = output["choices"][0]["text"].strip()

        if citations:
            answer_with_sources = f"{answer}\n\nSources:\n" + "\n".join(citations)
        else:
            answer_with_sources = answer

        conversation_memory.append(
            {"user": question, "assistant": answer_with_sources}
        )
        save_memory()

        return answer_with_sources, display_conversation()
    except Exception as error:
        traceback.print_exc()
        return f"Chat failed: {error}", display_conversation()


# =========================
# GRADIO UI
# =========================

with gr.Blocks(title="Local Medical AI") as interface:
    gr.Markdown("# Local Medical AI")
    gr.Markdown(f"**Note:** {MEDICAL_WARNING}")

    with gr.Row():
        pdf_upload = gr.File(
            label="Upload PDF",
            file_types=[".pdf"],
            type="filepath",
        )
        upload_status = gr.Textbox(
            label="Index Status",
            interactive=False,
        )

    question = gr.Textbox(
        label="Question",
        lines=2,
        placeholder="Ask a question about the uploaded document...",
    )
    ask_button = gr.Button("Ask")

    answer_output = gr.Textbox(
        label="AI Answer",
        lines=12,
    )
    memory_output = gr.Textbox(
        label="Conversation Memory",
        lines=12,
        value=display_conversation,
    )

    pdf_upload.upload(
        fn=index_uploaded_pdf,
        inputs=pdf_upload,
        outputs=upload_status,
    )
    ask_button.click(
        fn=ask_ai,
        inputs=question,
        outputs=[answer_output, memory_output],
    )
    question.submit(
        fn=ask_ai,
        inputs=question,
        outputs=[answer_output, memory_output],
    )


def launch():
    interface.launch()


if __name__ == "__main__":
    launch()
