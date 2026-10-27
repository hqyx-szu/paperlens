import pytest
from pydantic import ValidationError

from app.core.config import Settings


def test_production_requires_secure_cookie() -> None:
    with pytest.raises(ValidationError):
        Settings(app_env="production", cookie_secure=False, _env_file=None)


def test_production_allows_secure_cookie() -> None:
    settings = Settings(app_env="production", cookie_secure=True, _env_file=None)

    assert settings.cookie_secure is True
