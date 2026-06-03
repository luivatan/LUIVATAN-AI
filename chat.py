from sentence_transformers import SentenceTransformer
import chromadb
from llama_cpp import Llama

# =========================
# EMBEDDING MODEL
# =========================

embed_model = SentenceTransformer('all-MiniLM-L6-v2')

# =========================
# LOAD LLM
# =========================

llm = Llama(
    model_path="/media/shaggvt/progames/lm/qwen2.5-coder-7b-instruct-q4_k_m.gguf",
    n_ctx=2048
)

# =========================
# LOAD DATABASE
# =========================

client = chromadb.PersistentClient(path="./database")

collection = client.get_or_create_collection(
    name="medical_docs"
)

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
    print("\n========== RETRIEVED TEXT ==========\n")
    print(retrieved_chunks[:3000])
    print("\n====================================\n")
    print("\n====================")
    print("RETRIEVED SOURCES")
    print("====================\n")

    for i, chunk in enumerate(results['documents'][0]):

        print(f"\nSOURCE {i+1}:\n")
        print(chunk[:500])

        prompt = f"""
        You are an advanced medical AI assistant.

        Answer the question using ONLY the retrieved medical information.

        Be specific and detailed.

        If the information is unclear, explain what was found.

        Retrieved Medical Information:
        {retrieved_chunks}

        Question:
        {query}

        Detailed Answer:
        """
    output = llm(
        prompt,
        max_tokens=300,
        temperature=0.2
    )

    answer = output["choices"][0]["text"]

    print("\nAI ANSWER:\n")
    print(answer)