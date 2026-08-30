"""Phase 79: ModelRouter selects an appropriate discovered model for a
task, ranked by size (a real, if imperfect, proxy for local inference
latency) and bounded by a configured ceiling. No network, no real models."""

from __future__ import annotations

import pytest

from apex_ai.config.settings import with_overrides
from apex_ai.models.manager import ModelManager
from apex_ai.models.router import ModelRouter


def _write_gguf(path, size_bytes: int) -> None:
    path.write_bytes(b"GGUF" + b"\x00" * max(0, size_bytes - 4))


def test_select_with_no_loadable_models_returns_none(settings):
    router = ModelRouter(ModelManager(settings))
    decision = router.select("chat")
    assert decision.entry is None
    assert decision.task == "chat"
    assert "No loadable model" in decision.reason


def test_select_chat_prefers_the_largest_loadable_model(settings, tmp_path):
    model_dir = tmp_path / "models"
    model_dir.mkdir()
    _write_gguf(model_dir / "small.gguf", 1_000)
    _write_gguf(model_dir / "large.gguf", 100_000)
    router = ModelRouter(ModelManager(with_overrides(settings, model_dir=model_dir)))

    decision = router.select("chat")

    assert decision.entry.name == "large.gguf"
    assert "quality" in decision.reason


def test_select_fast_prefers_the_smallest_loadable_model(settings, tmp_path):
    model_dir = tmp_path / "models"
    model_dir.mkdir()
    _write_gguf(model_dir / "small.gguf", 1_000)
    _write_gguf(model_dir / "large.gguf", 100_000)
    router = ModelRouter(ModelManager(with_overrides(settings, model_dir=model_dir)))

    decision = router.select("fast")

    assert decision.entry.name == "small.gguf"
    assert "latency" in decision.reason


def test_select_ignores_models_with_an_unknown_format(settings, tmp_path):
    model_dir = tmp_path / "models"
    model_dir.mkdir()
    (model_dir / "corrupted.gguf").write_bytes(b"NOTGGUF" + b"\x00" * 1000)
    _write_gguf(model_dir / "real.gguf", 500)
    router = ModelRouter(ModelManager(with_overrides(settings, model_dir=model_dir)))

    decision = router.select("chat")

    assert decision.entry.name == "real.gguf"


def test_select_fast_respects_a_configured_size_ceiling(settings, tmp_path):
    model_dir = tmp_path / "models"
    model_dir.mkdir()
    _write_gguf(model_dir / "small.gguf", 1_000)
    _write_gguf(model_dir / "huge.gguf", 50 * 1024 * 1024)
    manager = ModelManager(with_overrides(settings, model_dir=model_dir))
    router = ModelRouter(manager, max_fast_model_mb=10)

    decision = router.select("fast")

    assert decision.entry.name == "small.gguf"


def test_select_fast_refuses_when_nothing_fits_the_configured_ceiling(settings, tmp_path):
    model_dir = tmp_path / "models"
    model_dir.mkdir()
    _write_gguf(model_dir / "huge.gguf", 50 * 1024 * 1024)
    manager = ModelManager(with_overrides(settings, model_dir=model_dir))
    router = ModelRouter(manager, max_fast_model_mb=1)

    decision = router.select("fast")

    assert decision.entry is None
    assert "APEX_MAX_FAST_MODEL_MB" in decision.reason


def test_chat_task_ignores_the_fast_only_ceiling(settings, tmp_path):
    """The configured ceiling only constrains the 'fast' task - the 'chat'
    task must not be silently limited by a knob meant for something else."""
    model_dir = tmp_path / "models"
    model_dir.mkdir()
    _write_gguf(model_dir / "huge.gguf", 50 * 1024 * 1024)
    manager = ModelManager(with_overrides(settings, model_dir=model_dir))
    router = ModelRouter(manager, max_fast_model_mb=1)

    decision = router.select("chat")

    assert decision.entry.name == "huge.gguf"


def test_no_configured_ceiling_means_every_loadable_model_is_eligible(settings, tmp_path):
    model_dir = tmp_path / "models"
    model_dir.mkdir()
    _write_gguf(model_dir / "huge.gguf", 50 * 1024 * 1024)
    router = ModelRouter(ModelManager(with_overrides(settings, model_dir=model_dir)))
    assert router.max_fast_model_mb is None

    decision = router.select("fast")

    assert decision.entry.name == "huge.gguf"


def test_select_unknown_task_raises_value_error(settings):
    router = ModelRouter(ModelManager(settings))
    with pytest.raises(ValueError, match="Unknown routing task"):
        router.select("does-not-exist")


def test_settings_apex_max_fast_model_mb_env_var(monkeypatch):
    from apex_ai.config.settings import load_settings

    monkeypatch.setenv("APEX_MAX_FAST_MODEL_MB", "128")
    assert load_settings().max_fast_model_mb == 128.0


def test_settings_apex_max_fast_model_mb_defaults_to_no_ceiling(monkeypatch):
    from apex_ai.config.settings import load_settings

    monkeypatch.delenv("APEX_MAX_FAST_MODEL_MB", raising=False)
    assert load_settings().max_fast_model_mb is None


def test_settings_apex_max_fast_model_mb_rejects_a_nonpositive_value(monkeypatch):
    from apex_ai.config.settings import load_settings

    monkeypatch.setenv("APEX_MAX_FAST_MODEL_MB", "-5")
    assert load_settings().max_fast_model_mb is None
