from sentence_transformers import SentenceTransformer
import chromadb

from llama_cpp import Llama
from langchain_text_splitters import RecursiveCharacterTextSplitter
import os

# =========================
# LOAD EMBEDDING MODEL
# =========================

embed_model = SentenceTransformer('all-MiniLM-L6-v2')

# =========================
# LOAD LOCAL LLM
# =========================

llm = Llama(
    model_path="/media/shaggvt/progames/lm/qwen2.5-coder-7b-instruct-q4_k_m.gguf",
    n_ctx=2048
)

# =========================
# VECTOR DATABASE
# =========================
import shutil

if os.path.exists("./database"):
    shutil.rmtree("./database")

client = chromadb.PersistentClient(path="./database")

collection = client.get_or_create_collection(
    name="medical_docs"
)
# =========================
# CHUNKING
# =========================

splitter = RecursiveCharacterTextSplitter(
    chunk_size=1000,
    chunk_overlap=150
)

# =========================
# LOAD PDFs
# =========================

# =========================
# LOAD PDFs
# =========================

from pdf2image import convert_from_path
import pytesseract

pdf_folder = "pdfs"

doc_id = 0

for filename in os.listdir(pdf_folder):

    if filename.endswith(".pdf"):

        path = os.path.join(pdf_folder, filename)

        print(f"\nPROCESSING PDF: {filename}")

        full_text = ""

        pages = convert_from_path(path)

        for page in pages:

            text = pytesseract.image_to_string(page)

            if text:

                full_text += text

        print("TEXT LENGTH:", len(full_text))

        chunks = splitter.split_text(full_text)

        print("NUMBER OF CHUNKS:", len(chunks))

        for chunk in chunks:

            chunk = chunk.strip()

            if len(chunk) > 100:

                print("ADDING:", chunk[:80])

                embedding = embed_model.encode(chunk).tolist()

                collection.add(
                documents=[chunk],
                embeddings=[embedding],
                ids=[str(doc_id)],
                metadatas=[{"source": filename}]
            )               

                doc_id += 1

# =========================
# CHAT LOOP
# =========================
while True:

    query = input("\nAsk medical question: ")

    query_embedding = embed_model.encode(query).tolist()

    results = collection.query(
        query_embeddings=[query_embedding],
        n_results=3
    )

    retrieved_chunks = "\n".join(
        results['documents'][0]
    )

    print("\n====================")
    print("RETRIEVED SOURCES")
    print("====================\n")

    for i, chunk in enumerate(results['documents'][0]):

        print(f"\nSOURCE {i+1}:\n")
        print(chunk[:500])

    prompt = f"""
You are a medical assistant.

ONLY answer using the provided medical information.

Medical Information:
{retrieved_chunks}

Question:
{query}

Answer:
"""

    output = llm(
        prompt,
        max_tokens=300,
        temperature=0.2
    )

    answer = output["choices"][0]["text"]

    print("\nAI ANSWER:\n")
    print(answer)