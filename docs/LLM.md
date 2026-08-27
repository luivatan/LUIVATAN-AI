# Apex AI LLM system (phases 31–40)

`apex_llm.py` provides a single provider-neutral generation contract. It supports local llama.cpp GGUF models, Ollama, and OpenAI-compatible APIs; model initialization is lazy and failures are converted to safe `LLMError` messages.

`ModelConfig.from_env()` centralizes model selection, context size, temperature, token limits, endpoint, and GPU layer configuration. `ModelManager` discovers `.gguf` files, validates selection, and invalidates its cache when configuration changes. `stream_text()` gives the UI incremental output for providers that return a complete response; native streaming can be added behind the same interface.

`ConversationEngine` owns bounded conversation history and constructs grounded prompts. It rejects empty questions and empty model output rather than silently presenting unreliable results. GPU acceleration is opt-in through `LLM_GPU_LAYERS`; zero keeps llama.cpp CPU-only.
