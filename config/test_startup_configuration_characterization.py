"""Regression coverage for startup dotenv resolution, isolated from real keys."""

import logging
from pathlib import Path

import pytest

from config.settings import Settings
from config import settings as settings_module


@pytest.fixture
def launch_layout(tmp_path, monkeypatch):
    # Keep real credentials and inherited process configuration out of the probe.
    for name in Settings.model_fields:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("DEEPSEEEK_API_KEY", raising=False)
    project = tmp_path / "Lazzaro"
    project.mkdir()
    (project / ".env").write_text(
        "AI_PROVIDER=gemini\n"
        "AI_FALLBACK_ORDER=gemini\n"
        "LLM_FALLBACKS_ENABLED=false\n"
        "GEMINI_API_KEY=synthetic-characterization-key\n",
        encoding="utf-8",
    )
    monkeypatch.setitem(Settings.model_config, "env_file", project / ".env")
    monkeypatch.setattr(settings_module, "PROJECT_ROOT", project)
    return tmp_path, project


def test_default_env_path_is_anchored_to_project():
    assert Settings.model_config["env_file"] == Path(__file__).resolve().parents[1] / ".env"
    assert Settings.model_config["env_file"].is_absolute()


def test_parent_launch_loads_project_env(launch_layout, monkeypatch):
    parent, project = launch_layout
    monkeypatch.chdir(parent)

    settings = Settings()

    assert (project / ".env").is_file()
    assert not Path(".env").exists()
    assert settings.AI_PROVIDER == "gemini"
    assert settings.AI_FALLBACK_ORDER == "gemini"
    assert settings.LLM_FALLBACKS_ENABLED is False
    assert settings.GEMINI_API_KEY == "synthetic-characterization-key"
    assert Path(settings.CHROMA_DB_PATH) == project / "lazzaro_data"


def test_project_launch_loads_project_env(launch_layout, monkeypatch):
    _, project = launch_layout
    monkeypatch.chdir(project)

    settings = Settings()

    assert settings.GEMINI_API_KEY == "synthetic-characterization-key"
    assert settings.AI_FALLBACK_ORDER == "gemini"
    assert settings.LLM_FALLBACKS_ENABLED is False
    assert Path(settings.CHROMA_DB_PATH).resolve() == project / "lazzaro_data"


def test_parent_launch_constructs_configured_gemini_client(
    launch_layout, monkeypatch,
):
    parent, _ = launch_layout
    monkeypatch.chdir(parent)
    # Avoid the application logger opening a runtime log during module import.
    from unittest.mock import Mock, patch
    with patch.object(logging.getLogger(), "handlers", [logging.NullHandler()]):
        from ai_engine import client_factory

    gemini = Mock()
    monkeypatch.setattr(client_factory, "GeminiClient", gemini)
    facade = Mock()
    monkeypatch.setattr(client_factory, "FallbackAIClient", facade)
    constructors = []
    for name in ("OllamaClient", "OpenAICompatibleClient"):
        constructor = Mock(side_effect=AssertionError("Unexpected client construction"))
        monkeypatch.setattr(client_factory, name, constructor)
        constructors.append(constructor)

    settings = Settings()
    assert client_factory._resolver_orden_proveedores(settings) == ["gemini"]
    assert client_factory.build_ai_client(settings) is facade.return_value
    gemini.assert_called_once_with(settings=settings)
    facade.assert_called_once_with([gemini.return_value], settings=settings)
    for constructor in constructors:
        constructor.assert_not_called()


def test_cwd_env_cannot_replace_project_env(launch_layout, monkeypatch):
    parent, _ = launch_layout
    (parent / ".env").write_text("GEMINI_API_KEY=wrong-cwd-key\n", encoding="utf-8")
    monkeypatch.chdir(parent)
    assert Settings().GEMINI_API_KEY == "synthetic-characterization-key"


def test_environment_still_overrides_dotenv(launch_layout, monkeypatch):
    parent, _ = launch_layout
    monkeypatch.chdir(parent)
    monkeypatch.setenv("GEMINI_API_KEY", "synthetic-process-key")
    assert Settings().GEMINI_API_KEY == "synthetic-process-key"


def test_explicit_env_file_and_disable_still_work(launch_layout, monkeypatch):
    parent, _ = launch_layout
    monkeypatch.chdir(parent)
    custom = parent / "custom.env"
    custom.write_text("GEMINI_API_KEY=synthetic-custom-key\n", encoding="utf-8")
    assert Settings(_env_file=custom).GEMINI_API_KEY == "synthetic-custom-key"
    assert Settings(_env_file=None).GEMINI_API_KEY is None


def test_data_paths_are_identical_from_both_launch_directories(launch_layout, monkeypatch):
    parent, project = launch_layout
    for cwd in (parent, project):
        monkeypatch.chdir(cwd)
        settings = Settings()
        assert Path(settings.CHROMA_DB_PATH) == project / "lazzaro_data"
        assert Path(settings.ACTION_LEDGER_PATH) == project / "lazzaro_data/action_ledger.sqlite3"
        assert Path(settings.MEMORY_WRITE_LEDGER_PATH) == project / "lazzaro_data/memory_write_ledger.sqlite3"


@pytest.mark.parametrize("field", [
    "CHROMA_DB_PATH", "ACTION_LEDGER_PATH", "MEMORY_WRITE_LEDGER_PATH",
])
def test_custom_relative_and_absolute_data_paths(launch_layout, monkeypatch, field):
    parent, project = launch_layout
    monkeypatch.chdir(parent)
    relative = Settings(_env_file=None, **{field: "custom/data"})
    assert Path(getattr(relative, field)) == project / "custom/data"
    absolute_path = parent / "external/data"
    absolute = Settings(_env_file=None, **{field: str(absolute_path)})
    assert Path(getattr(absolute, field)) == absolute_path


def test_in_memory_ledgers_remain_in_memory(launch_layout):
    settings = Settings(
        _env_file=None,
        ACTION_LEDGER_PATH=":memory:",
        MEMORY_WRITE_LEDGER_PATH=":memory:",
    )
    assert settings.ACTION_LEDGER_PATH == ":memory:"
    assert settings.MEMORY_WRITE_LEDGER_PATH == ":memory:"
