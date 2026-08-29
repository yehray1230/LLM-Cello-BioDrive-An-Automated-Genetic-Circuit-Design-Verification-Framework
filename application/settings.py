from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import litellm

from application.secret_store import SecretStore, create_secret_store


DEFAULT_SETTINGS = {
    "provider": "OpenAI",
    "model_name": "gpt-5.4-mini",
    "api_base": "",
    "cello_command": "",
    "cello_artifact_format": "cello_v2",
    "ucf_path": "",
    "sensor_path": "",
    "device_path": "",
    "default_host": "Escherichia coli",
    "default_compute_budget": 6,
}


class SettingsService:
    def __init__(
        self,
        settings_path: Path,
        secret_store: SecretStore | None = None,
    ):
        self.settings_path = settings_path
        self.secret_store = secret_store or create_secret_store(settings_path)

    def load_settings(self) -> dict[str, Any]:
        """Load public settings and resolve the API key from the secret store."""
        settings = dict(DEFAULT_SETTINGS)
        data: dict[str, Any] = {}
        if self.settings_path.exists():
            try:
                loaded = json.loads(self.settings_path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    data = loaded
            except Exception:
                data = {}

        for key in DEFAULT_SETTINGS:
            if key in data:
                val = data[key]
                if isinstance(DEFAULT_SETTINGS[key], int):
                    try:
                        val = int(val)
                    except (ValueError, TypeError):
                        val = DEFAULT_SETTINGS[key]
                settings[key] = val

        if settings["cello_artifact_format"] not in {"cello_v2", "cello21"}:
            raise ValueError(
                "cello_artifact_format must be 'cello_v2' or 'cello21'"
            )

        # One-time migration from the legacy plaintext settings file.
        legacy_key = str(data.get("api_key") or "").strip()
        if legacy_key:
            self.secret_store.set(legacy_key)
            self._write_public_settings(settings)

        settings["api_key"] = self._load_secret()
        return settings

    def save_settings(self, settings: dict[str, Any]) -> None:
        """Save public settings separately from the API key."""
        self.load_settings()  # Migrate any legacy plaintext key before overwriting.
        cello_artifact_format = str(
            settings.get("cello_artifact_format") or "cello_v2"
        ).strip()
        if cello_artifact_format not in {"cello_v2", "cello21"}:
            raise ValueError(
                "cello_artifact_format must be 'cello_v2' or 'cello21'"
            )
        public_settings = {
            "provider": str(settings.get("provider") or "OpenAI").strip(),
            "model_name": str(settings.get("model_name") or "gpt-5.4-mini").strip(),
            "api_base": str(settings.get("api_base") or "").strip(),
            "cello_command": str(settings.get("cello_command") or "").strip(),
            "cello_artifact_format": cello_artifact_format,
            "ucf_path": str(settings.get("ucf_path") or "").strip(),
            "sensor_path": str(settings.get("sensor_path") or "").strip(),
            "device_path": str(settings.get("device_path") or "").strip(),
            "default_host": str(settings.get("default_host") or "Escherichia coli").strip(),
            "default_compute_budget": int(settings.get("default_compute_budget") or 6),
        }
        api_key = str(settings.get("api_key") or "").strip()
        self._write_public_settings(public_settings)
        if api_key and not self._is_masked(api_key):
            self.secret_store.set(api_key)

    def clear_api_key(self) -> None:
        self.secret_store.clear()

    def storage_status(self) -> dict[str, Any]:
        return {
            "name": self.secret_store.name,
            "persistent": self.secret_store.persistent,
        }

    def get_settings_masked(self) -> dict[str, Any]:
        settings = self.load_settings()
        key = settings.get("api_key", "").strip()
        system_key_configured = bool(
            os.getenv("LITELLM_API_KEY") or os.getenv("OPENAI_API_KEY")
        )
        if key:
            settings["api_key"] = (
                f"{key[:4]}...{key[-4:]}" if len(key) > 8 else "sk-..."
            )
        else:
            settings["api_key"] = ""
        settings["api_key_configured"] = bool(key)
        settings["model_credentials_configured"] = bool(key) or system_key_configured
        settings["model_credentials_mode"] = (
            "byok" if key else ("system_default" if system_key_configured else "none")
        )
        settings["credential_storage"] = self.storage_status()
        return settings

    def get_settings_raw(self) -> dict[str, Any]:
        return self.load_settings()

    def _load_secret(self) -> str:
        try:
            return self.secret_store.get().strip()
        except Exception:
            return ""

    def _write_public_settings(self, settings: dict[str, Any]) -> None:
        self.settings_path.parent.mkdir(parents=True, exist_ok=True)
        public_settings = {}
        for key, default in DEFAULT_SETTINGS.items():
            val = settings.get(key)
            if val is None:
                val = default
            if isinstance(default, int):
                try:
                    public_settings[key] = int(val)
                except (ValueError, TypeError):
                    public_settings[key] = default
            else:
                public_settings[key] = str(val).strip()
        temporary = self.settings_path.with_suffix(f"{self.settings_path.suffix}.tmp")
        temporary.write_text(
            json.dumps(public_settings, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        temporary.replace(self.settings_path)

    @staticmethod
    def _is_masked(value: str) -> bool:
        return "..." in value

    def check_availability(
        self,
        temp_settings: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        settings = temp_settings if temp_settings is not None else self.load_settings()
        api_key = str(settings.get("api_key") or "").strip()
        if self._is_masked(api_key):
            api_key = str(self.load_settings().get("api_key") or "").strip()
        model_name = str(settings.get("model_name") or "").strip()
        api_base = str(settings.get("api_base") or "").strip()

        resolved_key = api_key or os.getenv("LITELLM_API_KEY") or os.getenv("OPENAI_API_KEY")
        resolved_model = (
            model_name
            or os.getenv("LITELLM_MODEL")
            or os.getenv("OPENAI_MODEL")
            or "gpt-5.4-mini"
        )
        resolved_base = (
            api_base
            or os.getenv("LITELLM_API_BASE")
            or os.getenv("OPENAI_API_BASE")
            or None
        )
        mode = "byok" if api_key else ("system_default" if resolved_key else "none")

        if not resolved_key:
            return {
                "available": False,
                "mode": mode,
                "provider": settings.get("provider", "OpenAI"),
                "model_name": resolved_model,
                "message": "No API Key configured. Please set your API Key.",
            }

        try:
            litellm.completion(
                model=resolved_model,
                messages=[{"role": "user", "content": "ping"}],
                max_tokens=2,
                api_key=resolved_key,
                api_base=resolved_base,
                timeout=4.0,
                caching=False,
            )
            return {
                "available": True,
                "mode": mode,
                "provider": settings.get("provider", "OpenAI"),
                "model_name": resolved_model,
                "message": "Connection successful!",
            }
        except Exception:
            return {
                "available": False,
                "mode": mode,
                "provider": settings.get("provider", "OpenAI"),
                "model_name": resolved_model,
                "message": (
                    "Connection failed. Verify the provider, model, endpoint, "
                    "and API key."
                ),
            }
