import os
from unittest.mock import patch


def test_default_settings():
    """Settings should have sensible defaults when no env vars or .env file."""
    with patch.dict(os.environ, {}, clear=True):
        from importlib import reload
        import ideascroller.config as config_module
        reload(config_module)
        # Pass _env_file=None to skip reading .env from disk
        settings = config_module.Settings(_env_file=None)

    assert settings.comment_threshold == 300
    assert settings.host == "127.0.0.1"
    assert settings.port == 8000
    assert settings.anthropic_api_key == ""
    assert settings.chrome_user_data_dir == ""


def test_settings_from_env():
    env = {
        "ANTHROPIC_API_KEY": "sk-test-key",
        "CHROME_USER_DATA_DIR": "/path/to/chrome",
        "COMMENT_THRESHOLD": "500",
        "HOST": "0.0.0.0",
        "PORT": "9000",
    }
    with patch.dict(os.environ, env, clear=True):
        from importlib import reload
        import ideascroller.config as config_module
        reload(config_module)
        settings = config_module.Settings()

    assert settings.anthropic_api_key == "sk-test-key"
    assert settings.chrome_user_data_dir == "/path/to/chrome"
    assert settings.comment_threshold == 500
    assert settings.host == "0.0.0.0"
    assert settings.port == 9000


def test_default_chrome_path_macos():
    from ideascroller.config import get_chrome_user_data_dir
    import platform

    with patch.dict(os.environ, {}, clear=True):
        from importlib import reload
        import ideascroller.config as config_module
        reload(config_module)
        settings = config_module.Settings()

    path = get_chrome_user_data_dir(settings)
    if platform.system() == "Darwin":
        assert "Library/Application Support/Google/Chrome" in path
