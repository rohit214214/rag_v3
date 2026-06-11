from pathlib import Path
import sys

# Allow `python config/settings.py` (repo root not always on sys.path).
_root = Path(__file__).resolve().parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

import os
from dataclasses import dataclass

from config.cf import load_config, loaded_dotenv_path

# Load cf.env, then .env, then .env.example (first file that exists).
# Override with env var CONFIG_DOTENV_FILE=/abs/path/to/file.env
load_config()

def _env(key: str, default: str = "") -> str:
    """Read env var, strip whitespace and optional surrounding quotes."""
    raw = os.getenv(key)
    if raw is None:
        return default
    value = raw.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        value = value[1:-1].strip()
    return value


def _to_int(value: str | None, default: int) -> int:
    if value is None:
        return default
    cleaned = str(value).strip().strip("'\"")
    if not cleaned:
        return default
    try:
        return int(cleaned)
    except (TypeError, ValueError):
        return default


def _normalize_hologres_host(host: str) -> str:
    """Hologres expects a hostname, not an http(s) MaxCompute API URL."""
    host = host.strip()
    for prefix in ("https://", "http://"):
        if host.lower().startswith(prefix):
            host = host[len(prefix) :]
    return host.split("/")[0].strip()


def _normalize_postgres_url(raw: str) -> str | None:
    """
    Turn HOLOGRES_URL into a SQLAlchemy URL, or None to use HOST/PORT/DB fields.

    Accepts: postgresql://, postgres://, postgresql+psycopg2://, jdbc:postgresql://
    Ignores: empty values, hostname-only (no ://), placeholders.
    """
    value = raw.strip()
    if not value:
        return None

    lowered = value.lower()
    if lowered in {"none", "null", "-", "your-hologres-url", "optional"}:
        return None

    if lowered.startswith("jdbc:"):
        value = value[5:].strip()
        lowered = value.lower()

    if lowered.startswith("postgres://"):
        value = "postgresql://" + value[len("postgres://") :]
    elif not lowered.startswith(("postgresql://", "postgresql+psycopg2://")):
        # Hostname only or unknown scheme — use HOLOGRES_HOST instead.
        if "://" not in value:
            return None
        return None

    if value.startswith("postgresql://"):
        value = "postgresql+psycopg2://" + value[len("postgresql://") :]
    return value


def _api_key(*keys: str) -> str:
    for key in keys:
        value = _env(key)
        if value:
            return value
    return ""


def _default_dashscope_endpoint() -> str:
    region = _env("DASHSCOPE_REGION", "intl").lower()
    if region in ("cn", "china", "cn-hangzhou", "hangzhou"):
        return "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
    return "https://dashscope-intl.aliyuncs.com/compatible-mode/v1/chat/completions"


def _normalize_chat_completions_endpoint(url: str) -> str:
    """
    DashScope OpenAI-compatible chat API path must end with /chat/completions.
  Many consoles show only the base .../compatible-mode/v1 — we append the rest.
    """
    value = url.strip().rstrip("/")
    if not value:
        return _default_dashscope_endpoint()
    if value.endswith("/chat/completions"):
        return value
    if value.endswith("/compatible-mode/v1") or value.endswith("/v1"):
        return f"{value}/chat/completions"
    return value


@dataclass(frozen=True)
class Settings:
    # Optional: paste full URL from Hologres console (PostgreSQL protocol). If set, host/port/db below are ignored.
    hologres_url: str = _env("HOLOGRES_URL")
    hologres_host: str = _normalize_hologres_host(_env("HOLOGRES_HOST"))
    hologres_port: int = _to_int(_env("HOLOGRES_PORT", ""), 80)
    hologres_db: str = _env("HOLOGRES_DB")
    hologres_user: str = _env("HOLOGRES_USER")
    hologres_password: str = _env("HOLOGRES_PASSWORD")
    hologres_sslmode: str = _env("HOLOGRES_SSLMODE", "require")
    app_max_rows: int = _to_int(_env("APP_MAX_ROWS", ""), 10000)
    app_query_timeout_sec: int = _to_int(_env("APP_QUERY_TIMEOUT_SEC", ""), 120)
    app_default_schema: str = _env("APP_DEFAULT_SCHEMA", "public")
    app_business_rules_path: str = _env("APP_BUSINESS_RULES_PATH", "data/business_rules.json")
    app_table_whitelist: str = _env("APP_TABLE_WHITELIST")
    dashscope_region: str = _env("DASHSCOPE_REGION", "intl")
    qwen_endpoint: str = _normalize_chat_completions_endpoint(
        _env("QWEN_ENDPOINT") or _default_dashscope_endpoint()
    )
    qwen_api_key: str = _api_key("QWEN_API_KEY", "DASHSCOPE_API_KEY")
    qwen_model: str = _env("QWEN_MODEL", "qwen-plus")
    v_endpoint: str = _normalize_chat_completions_endpoint(
        _env("V_ENDPOINT") or _env("QWEN_ENDPOINT") or _default_dashscope_endpoint()
    )
    v_api_key: str = _api_key("V_API_KEY", "QWEN_API_KEY", "DASHSCOPE_API_KEY")
    v_model: str = _env("V_MODEL", "qwen-turbo")

    @property
    def whitelist_tables(self) -> list[str]:
        if not self.app_table_whitelist.strip():
            return []
        return [item.strip() for item in self.app_table_whitelist.split(",") if item.strip()]

    def resolved_hologres_url(self) -> str | None:
        return _normalize_postgres_url(self.hologres_url)

    def uses_hologres_url(self) -> bool:
        return self.resolved_hologres_url() is not None

    @property
    def hologres_display_target(self) -> str:
        if self.uses_hologres_url():
            return "(from HOLOGRES_URL)"
        return f"{self.hologres_host}:{self.hologres_port}/{self.hologres_db}"

    def validate_hologres(self) -> list[str]:
        errors: list[str] = []
        resolved = self.resolved_hologres_url()
        if resolved:
            if "maxcompute.aliyun.com" in resolved.lower():
                errors.append(
                    "HOLOGRES_URL must be the Hologres PostgreSQL connection string, "
                    "not a MaxCompute HTTP/API endpoint."
                )
            return errors

        # HOLOGRES_URL set but not a full postgres URL — fall back to HOST/PORT (no error here).
        if not self.hologres_host:
            errors.append("HOLOGRES_HOST is missing.")
        if self.hologres_port <= 0:
            errors.append("HOLOGRES_PORT must be a positive integer (default 80).")
        if not self.hologres_db:
            errors.append("HOLOGRES_DB is missing.")
        if not self.hologres_user:
            errors.append("HOLOGRES_USER is missing.")
        if "maxcompute.aliyun.com" in self.hologres_host.lower():
            errors.append(
                "HOLOGRES_HOST is a MaxCompute endpoint, not Hologres. "
                "In Alibaba Cloud: Hologres instance -> Connection info -> PostgreSQL -> copy Hostname "
                "(example: hgpostcn-xxxxx-cn-hangzhou.hologres.aliyuncs.com). "
                "Or set HOLOGRES_URL to the full postgresql://... string from that page."
            )
        return errors


settings = Settings()


if __name__ == "__main__":
    print("Loaded env from:", loaded_dotenv_path() or "(none — create cf.env or .env)")
    print(settings)
    holo_errors = settings.validate_hologres()
    if holo_errors:
        print("Hologres config issues:")
        for item in holo_errors:
            print(" -", item)
    else:
        mode = "HOLOGRES_URL" if settings.uses_hologres_url() else "HOST/PORT/DB"
        print("Hologres config OK:", settings.hologres_display_target, f"({mode})")
        if settings.hologres_url.strip() and not settings.uses_hologres_url():
            print(
                "Note: HOLOGRES_URL is set but not a full postgresql:// URL — using HOLOGRES_HOST instead. "
                "Remove or comment HOLOGRES_URL if you only use separate fields."
            )