from __future__ import annotations

import re
from enum import Enum

import keyring
from keyring.errors import PasswordDeleteError

_SERVICE_NAME = "ai_screen"


class Provider(str, Enum):
    GEMINI = "gemini"
    CHATGPT = "chatgpt"
    CLAUDE = "claude"
    COPILOT = "copilot"
    DEEPSEEK = "deepseek"


class KeyStorageError(Exception):
    """Lỗi chung của tầng KeyStorage, để logic/UI phía trên bắt (catch) riêng."""


class KeyStorage:

    def save_key(self, provider: Provider, api_key: str) -> None:
        cleaned = self._validate_and_clean(api_key)
        try:
            keyring.set_password(_SERVICE_NAME, provider.value, cleaned)
        except Exception as exc:
            raise KeyStorageError(
                f"Không thể lưu API key cho {provider.value}: {exc}"
            ) from exc

    def get_key(self, provider: Provider) -> str | None:
        try:
            return keyring.get_password(_SERVICE_NAME, provider.value)
        except Exception as exc:
            raise KeyStorageError(
                f"Không thể đọc API key cho {provider.value}: {exc}"
            ) from exc

    def has_key(self, provider: Provider) -> bool:
        return self.get_key(provider) is not None

    def delete_key(self, provider: Provider) -> None:
        try:
            keyring.delete_password(_SERVICE_NAME, provider.value)
        except PasswordDeleteError:
            pass
        except Exception as exc:
            raise KeyStorageError(
                f"Không thể xoá API key cho {provider.value}: {exc}"
            ) from exc

    def list_configured_providers(self) -> list[Provider]:
        return [p for p in Provider if self.has_key(p)]

    @staticmethod
    def _validate_and_clean(api_key: str) -> str:
        if api_key is None:
            raise KeyStorageError("API key không được để trống")

        cleaned = api_key.strip()

        if not cleaned:
            raise KeyStorageError("API key không được để trống")

        if re.search(r"\s", cleaned):
            raise KeyStorageError(
            )

        return cleaned
    