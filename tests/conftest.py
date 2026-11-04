"""Tests must not read the developer's credentials, cookies or installed models."""
import pytest

from scripts import access, setup


@pytest.fixture(autouse=True)
def isolated_user_config(tmp_path_factory, monkeypatch):
    home = tmp_path_factory.mktemp("test-home")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.setenv("APPDATA", str(home / "AppData"))
    for name in ("GROQ_API_KEY", "OPENAI_API_KEY", "WHISPER_CPP_MODEL", "SETUP_COMPLETE"):
        monkeypatch.delenv(name, raising=False)
    config = home / ".config" / "watch"
    monkeypatch.setattr(setup, "CONFIG_DIR", config)
    monkeypatch.setattr(setup, "ENV_PATH", config / ".env")
    monkeypatch.setattr(setup, "LIBRARY_ROOT", home / "watch" / "library")
    monkeypatch.setattr(setup, "DEFAULT_LOCAL_MODEL", config / "models" / "ggml-base.en.bin")
    monkeypatch.setattr(setup, "EXTRA_LOCAL_MODEL_PATHS", ())
    monkeypatch.setattr(access, "_browser_spec", None)
