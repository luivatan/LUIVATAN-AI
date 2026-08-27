import pytest
from apex_llm import ConversationEngine, LLMError, ModelConfig, ModelManager, stream_text


def test_config_from_env(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setenv("LLM_GPU_LAYERS", "12")
    assert ModelConfig.from_env().n_gpu_layers == 12
    assert ModelConfig.from_env().provider == "ollama"


def test_model_manager_rejects_non_gguf(tmp_path):
    path = tmp_path / "model.bin"
    path.write_bytes(b"x")
    with pytest.raises(LLMError):
        ModelManager(tmp_path).select(path)


def test_streaming_and_conversation():
    assert list(stream_text("one two three", 2)) == ["one two ", "three"]
    engine = ConversationEngine(lambda prompt: "Grounded answer [1]")
    assert engine.ask("What?", "[1] evidence") == "Grounded answer [1]"
    assert engine.history[0]["user"] == "What?"


def test_empty_question_is_safe():
    with pytest.raises(LLMError, match="question"):
        ConversationEngine(lambda _: "x").ask(" ", "context")
