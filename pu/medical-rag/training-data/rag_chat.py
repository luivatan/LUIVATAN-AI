from llama_cpp import Llama
import os
import sys


def main():
    model_path = os.environ.get("LLAMA_MODEL_PATH", "YOUR_MODEL_PATH.gguf")
    if model_path == "YOUR_MODEL_PATH.gguf":
        print(
            "Warning: model path is placeholder. Set LLAMA_MODEL_PATH or edit the script.",
            file=sys.stderr,
        )

    try:
        llm = Llama(model_path=model_path, n_ctx=2048)
    except Exception as e:
        print(f"Failed to load model: {e}", file=sys.stderr)
        sys.exit(1)

    # Example retrieved chunks
    retrieved_text = """
    Burns should be cooled with running water for at least 20 minutes.
    Severe burns require emergency medical attention.
    """

    question = input("Ask question: ").strip()
    if not question:
        print("No question provided. Exiting.")
        return

    prompt = f"""
    You are a medical assistant.

    Use ONLY the provided medical information.

    Medical Information:
    {retrieved_text}

    Question:
    {question}

    Answer:
    """

    try:
        output = llm(
            prompt,
            max_tokens=200,
            temperature=0.2,
        )
    except Exception as e:
        print(f"LLM call failed: {e}", file=sys.stderr)
        sys.exit(1)

    # Extract text safely (compatible with different llama-cpp-python versions)
    text = None
    if isinstance(output, dict):
        choices = output.get("choices")
        if choices and isinstance(choices, list):
            first = choices[0]
            if isinstance(first, dict) and "text" in first:
                text = first["text"]
            else:
                text = str(first)
        else:
            text = str(output)
    else:
        text = str(output)

    print(text)


if __name__ == "__main__":
    main()
