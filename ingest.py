import hashlib
import json
import os
import shutil
from pathlib import Path


try:
    import chromadb
    import gradio as gr
    import requests
    from sentence_transformers import SentenceTransformer
except ModuleNotFoundError as error:
    print(
        f"Required package missing: {error.name}\n\n"
        "Run:\n"
        "pip install -r requirements.txt"
    )
    raise SystemExit(1) from error

DATABASE_PATH = "./database"
COLLECTION_NAME = "medical_docs"
MEMORY_PATH = Path("conversation_memory.json")
UPLOAD_DIR = Path("uploaded_pdfs")
MODEL_DIR = Path("models")
CHUNK_SIZE = 1000
CHUNK_OVERLAP = 150
MEMORY_LIMIT = 8
LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "llama_cpp").lower()
LLAMA_MODEL_PATH = os.environ.get("LLAMA_MODEL_PATH", "")
LLM_CONTEXT_SIZE = int(os.environ.get("LLM_CONTEXT_SIZE", "2048"))
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5-coder:7b")
OPENAI_API_BASE = os.environ.get("OPENAI_API_BASE", "https://api.openai.com/v1")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4.1-mini")
HF_MODEL_PATH = os.environ.get("HF_MODEL_PATH", "Qwen/Qwen2.5-0.5B-Instruct")
current_llama_model_path = LLAMA_MODEL_PATH
generate_answer = None
loaded_llm_key = None
last_source_texts = {}
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


def log_error(context, error):
    print(f"{context}: {error}")

# =========================
# LOAD EMBEDDING MODEL
# =========================

embed_model = SentenceTransformer("all-MiniLM-L6-v2")

# =========================
# LLM SETUP
# =========================

def dependency_error(package_name):
    return (
        f"Required package missing: {package_name}\n\n"
        "Run:\n"
        "pip install -r requirements.txt"
    )


def get_llm_key():
    if LLM_PROVIDER == "llama_cpp":
        return (LLM_PROVIDER, current_llama_model_path, LLM_CONTEXT_SIZE)

    if LLM_PROVIDER == "ollama":
        return (LLM_PROVIDER, OLLAMA_URL, OLLAMA_MODEL)

    if LLM_PROVIDER in {"openai", "openai_compatible"}:
        return (LLM_PROVIDER, OPENAI_API_BASE, OPENAI_MODEL)

    if LLM_PROVIDER == "transformers":
        return (LLM_PROVIDER, HF_MODEL_PATH)

    return (LLM_PROVIDER,)


def get_model_status():
    if LLM_PROVIDER == "llama_cpp" and not Path(current_llama_model_path).exists():
        return "Model not found.\n\nSelect a GGUF model before chatting."

    if LLM_PROVIDER in {"openai", "openai_compatible"} and not OPENAI_API_KEY:
        return "OpenAI-compatible API key missing. Set OPENAI_API_KEY before chatting."

    return f"Ready to load `{LLM_PROVIDER}` when you ask a question."


def load_llm():
    if LLM_PROVIDER == "llama_cpp":
        if not Path(current_llama_model_path).exists():
            raise RuntimeError(
                "Model not found. Select a GGUF model before chatting."
            )

        try:
            from llama_cpp import Llama
        except ModuleNotFoundError as error:
            raise RuntimeError(dependency_error("llama_cpp_python")) from error

        model = Llama(
            model_path=current_llama_model_path,
            n_ctx=LLM_CONTEXT_SIZE,
        )

        def generate(prompt, max_tokens=500, temperature=0.2):
            output = model(
                prompt,
                max_tokens=max_tokens,
                temperature=temperature,
            )

            return output["choices"][0]["text"].strip()

        return generate

    if LLM_PROVIDER == "ollama":
        def generate(prompt, max_tokens=500, temperature=0.2):
            response = requests.post(
                f"{OLLAMA_URL.rstrip('/')}/api/generate",
                json={
                    "model": OLLAMA_MODEL,
                    "prompt": prompt,
                    "stream": False,
                    "options": {
                        "num_predict": max_tokens,
                        "temperature": temperature,
                    },
                },
                timeout=180,
            )
            response.raise_for_status()

            return response.json().get("response", "").strip()

        return generate

    if LLM_PROVIDER in {"openai", "openai_compatible"}:
        if not OPENAI_API_KEY:
            raise RuntimeError(
                "OPENAI_API_KEY is required when LLM_PROVIDER=openai or openai_compatible."
            )

        def generate(prompt, max_tokens=500, temperature=0.2):
            response = requests.post(
                f"{OPENAI_API_BASE.rstrip('/')}/chat/completions",
                headers={
                    "Authorization": f"Bearer {OPENAI_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": OPENAI_MODEL,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                },
                timeout=180,
            )
            response.raise_for_status()

            return response.json()["choices"][0]["message"]["content"].strip()

        return generate

    if LLM_PROVIDER == "transformers":
        try:
            from transformers import pipeline
        except ModuleNotFoundError as error:
            raise RuntimeError(dependency_error("transformers")) from error

        pipe = pipeline(
            "text-generation",
            model=HF_MODEL_PATH,
            device_map="auto",
        )

        def generate(prompt, max_tokens=500, temperature=0.2):
            output = pipe(
                prompt,
                max_new_tokens=max_tokens,
                temperature=temperature,
                do_sample=temperature > 0,
                return_full_text=False,
            )

            return output[0]["generated_text"].strip()

        return generate

    raise RuntimeError(
        "Unsupported LLM_PROVIDER. Use llama_cpp, ollama, openai_compatible, openai, or transformers."
    )


def get_answer_generator():
    global generate_answer, loaded_llm_key

    llm_key = get_llm_key()

    if generate_answer is None or loaded_llm_key != llm_key:
        generate_answer = load_llm()
        loaded_llm_key = llm_key

    return generate_answer


def select_gguf_model(model_file):
    global current_llama_model_path, generate_answer, loaded_llm_key

    if LLM_PROVIDER != "llama_cpp":
        return f"Current provider is `{LLM_PROVIDER}`. GGUF selection is only used with `llama_cpp`."

    if not model_file:
        return "Select a GGUF model file first."

    try:
        MODEL_DIR.mkdir(exist_ok=True)
        source_path = Path(model_file)
        saved_path = MODEL_DIR / safe_filename(source_path.name)
        shutil.copy2(source_path, saved_path)

        current_llama_model_path = str(saved_path)
        generate_answer = None
        loaded_llm_key = None

        return f"Selected GGUF model: {saved_path}"
    except Exception as error:
        log_error("Model selection failed", error)
        return f"Model selection failed: {error}"

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
# DOCUMENT LIBRARY
# =========================

def document_library():
    try:
        records = collection.get(include=["metadatas"])
    except Exception as error:
        log_error("Document library failed", error)
        return "Uploaded Documents\n\nUnable to read document library."

    documents = {}

    for metadata in records.get("metadatas", []):
        if not metadata:
            continue

        source = metadata.get("source", "Unknown document")
        page = metadata.get("page")

        if source not in documents:
            documents[source] = {"pages": set(), "chunks": 0}

        documents[source]["chunks"] += 1

        if page is not None:
            documents[source]["pages"].add(page)

    if not documents:
        return "Uploaded Documents\n\nNo documents indexed yet."

    lines = ["Uploaded Documents"]

    for source in sorted(documents):
        page_count = len(documents[source]["pages"])
        chunk_count = documents[source]["chunks"]
        detail = f"{page_count} pages, {chunk_count} chunks"
        lines.append(f"- OK {source} ({detail})")

    return "\n".join(lines)


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
            "Required package missing: pypdf\n\n"
            "Run:\n"
            "pip install -r requirements.txt"
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
        return "Upload a PDF first.", document_library()

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
            return f"No indexable text chunks were found in {filename}.", document_library()

        collection.upsert(
            ids=ids,
            documents=documents,
            embeddings=embeddings,
            metadatas=metadatas,
        )

        status = f"Indexed {len(documents)} chunks from {filename}."

        if not is_medical_document:
            status = f"{status}\n\nWarning: {MEDICAL_WARNING}"

        return status, document_library()
    except Exception as error:
        log_error("Indexing failed", error)
        return f"Indexing failed: {error}", document_library()


# =========================
# RETRIEVAL AND CHAT
# =========================

def citation_label(metadata):
    source = metadata.get("source", "unknown source")
    page = metadata.get("page")
    chunk = metadata.get("chunk")

    if page is not None:
        if chunk is not None:
            return f"{source} Page {page} Chunk {chunk}"

        return f"{source} Page {page}"

    return source


def format_retrieved_context(results):
    global last_source_texts

    documents = results.get("documents", [[]])[0]
    metadatas = results.get("metadatas", [[]])[0]
    ids = results.get("ids", [[]])[0]
    distances = results.get("distances", [[]])[0]

    context_blocks = []
    citations = []
    last_source_texts = {}

    print("\n====================")
    print("RETRIEVED CHUNKS")
    print("====================\n")

    for index, document in enumerate(documents, start=1):
        metadata = metadatas[index - 1] if index - 1 < len(metadatas) else {}
        doc_id = ids[index - 1] if index - 1 < len(ids) else "unknown"
        distance = distances[index - 1] if index - 1 < len(distances) else None
        citation = citation_label(metadata)
        source_choice = f"[{index}] {citation}"

        print(f"{source_choice} | ID: {doc_id} | Distance: {distance}")
        print(document[:1000])
        print()

        context_blocks.append(
            f"[{index}] {citation}\n{document}"
        )
        citations.append(source_choice)
        last_source_texts[source_choice] = document

    return "\n\n".join(context_blocks), citations


def view_source(source_label):
    if not source_label:
        return "Select a source to view its retrieved text."

    return last_source_texts.get(source_label, "Source text is not available for this answer.")


def ask_ai(question):
    if not question or not question.strip():
        return (
            "Ask a question first.",
            display_conversation(),
            gr.update(choices=[], value=None),
            "No source selected.",
        )

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
                gr.update(choices=[], value=None),
                "No retrieved sources.",
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

        answer_generator = get_answer_generator()
        answer = answer_generator(
            prompt,
            max_tokens=500,
            temperature=0.2,
        )

        if citations:
            answer_with_sources = f"{answer}\n\nSources:\n" + "\n".join(citations)
        else:
            answer_with_sources = answer

        conversation_memory.append(
            {"user": question, "assistant": answer_with_sources}
        )
        save_memory()

        first_source = citations[0] if citations else None
        first_source_text = view_source(first_source)

        return (
            answer_with_sources,
            display_conversation(),
            gr.update(choices=citations, value=first_source),
            first_source_text,
        )
    except Exception as error:
        log_error("Chat failed", error)
        return (
            f"Chat failed: {error}",
            display_conversation(),
            gr.update(choices=[], value=None),
            "No source selected.",
        )


# =========================
# GRADIO UI
# =========================

with gr.Blocks(title="Local Medical AI") as interface:
    gr.Markdown("# Local Medical AI")
    gr.Markdown(f"**LLM provider:** `{LLM_PROVIDER}`")
    gr.Markdown(f"**Note:** {MEDICAL_WARNING}")

    with gr.Row():
        gguf_model = gr.File(
            label="Select GGUF Model",
            file_types=[".gguf"],
            type="filepath",
        )
        model_status = gr.Textbox(
            label="Model Status",
            value=get_model_status(),
            interactive=False,
        )

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

    document_library_output = gr.Textbox(
        label="Uploaded Documents",
        lines=8,
        value=document_library(),
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
        value=display_conversation(),
    )
    source_selector = gr.Dropdown(
        label="Sources",
        choices=[],
        interactive=True,
    )
    source_viewer = gr.Textbox(
        label="Source Viewer",
        lines=10,
        value="Sources from the latest answer will appear here.",
        interactive=False,
    )

    gguf_model.upload(
        fn=select_gguf_model,
        inputs=gguf_model,
        outputs=model_status,
    )
    pdf_upload.upload(
        fn=index_uploaded_pdf,
        inputs=pdf_upload,
        outputs=[upload_status, document_library_output],
    )
    ask_button.click(
        fn=ask_ai,
        inputs=question,
        outputs=[answer_output, memory_output, source_selector, source_viewer],
    )
    question.submit(
        fn=ask_ai,
        inputs=question,
        outputs=[answer_output, memory_output, source_selector, source_viewer],
    )
    source_selector.change(
        fn=view_source,
        inputs=source_selector,
        outputs=source_viewer,
    )


def launch():
    interface.launch()


if __name__ == "__main__":
    launch()
