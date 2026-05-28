import json
import re
import time
from dataclasses import dataclass
from typing import Any

import requests

from config.settings import settings


PROMPT_TEMPLATE = """You are a SQL generator for Hologres (PostgreSQL dialect).
Output only SQL and nothing else.
Rules:
1) Return a single SELECT or WITH...SELECT statement.
2) Never return INSERT/UPDATE/DELETE/DDL.
3) Use explicit date filters for time requests.
4) Respect requested aggregation grain.
5) Use ONLY tables listed under ALLOWED TABLES in the context. Never reference other tables.
6) For sales/revenue questions, use the Default table for sales if specified in context.
7) Dimension text values (country, brand, etc.) are stored UPPERCASE; use uppercase string literals in filters.

Context:
{context}
"""


class LlmApiError(RuntimeError):
    """Raised when DashScope / compatible OpenAI API returns an error."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        endpoint: str = "",
        model: str = "",
        response_body: str = "",
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.endpoint = endpoint
        self.model = model
        self.response_body = response_body


@dataclass
class LlmResult:
    sql: str
    model_used: str
    latency_ms: int
    used_fallback: bool
    raw_response: dict[str, Any]
    # Token counts from the API response "usage" field
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


def _extract_token_usage(body: dict[str, Any]) -> tuple[int, int, int]:
    """Extract (prompt_tokens, completion_tokens, total_tokens) from the API response.
    DashScope returns a 'usage' key in every successful response - we were ignoring it."""
    usage = body.get("usage") or {}
    prompt = int(usage.get("prompt_tokens", 0))
    completion = int(usage.get("completion_tokens", 0))
    total = int(usage.get("total_tokens", prompt + completion))
    return prompt, completion, total


def _safe_response_json(response: requests.Response) -> dict[str, Any] | None:
    try:
        payload = response.json()
        return payload if isinstance(payload, dict) else None
    except (json.JSONDecodeError, requests.exceptions.JSONDecodeError, ValueError, TypeError):
        return None


def _parse_api_error(response: requests.Response) -> str:
    payload = _safe_response_json(response)
    if payload:
        err = payload.get("error")
        if isinstance(err, dict):
            return err.get("message") or err.get("code") or json.dumps(err)
        return str(payload.get("message") or json.dumps(payload))
    text = (response.text or "").strip()
    if not text:
        return f"(empty body, HTTP {response.status_code})"
    return text[:500]


def _hint_for_status(status_code: int, endpoint: str) -> str:
    if status_code == 401:
        return (
            "Check QWEN_API_KEY (or DASHSCOPE_API_KEY): valid sk- key from the same "
            "DashScope console region as your endpoint."
        )
    if status_code == 403:
        is_intl = "dashscope-intl" in endpoint.lower()
        other = (
            "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
            if is_intl
            else "https://dashscope-intl.aliyuncs.com/compatible-mode/v1/chat/completions"
        )
        return (
            "403 Forbidden usually means: (1) API key region does not match the endpoint — "
            f"try the other region endpoint, e.g. {other}; "
            "(2) model name not enabled on your account (try qwen-plus or qwen-turbo in QWEN_MODEL); "
            "(3) billing / model access not activated in DashScope console."
        )
    if status_code in (404, 405):
        return (
            "QWEN_ENDPOINT must be the chat completions URL, e.g. "
            "https://dashscope-intl.aliyuncs.com/compatible-mode/v1/chat/completions "
            "(not just .../compatible-mode/v1)."
        )
    return ""


class LlmClient:
    def __init__(self) -> None:
        self.session = requests.Session()

    @staticmethod
    def _extract_sql(text: str) -> str:
        cleaned = text.strip()
        cleaned = re.sub(r"^```sql\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"^```\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
        return cleaned.strip()

    def _call_model(self, endpoint: str, api_key: str, model: str, prompt: str) -> tuple[str, dict[str, Any], int]:
        if not api_key.strip():
            raise LlmApiError(
                "API key is empty. Set QWEN_API_KEY or DASHSCOPE_API_KEY in cf.env.",
                endpoint=endpoint,
                model=model,
            )

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key.strip()}",
        }
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": "You generate SQL only."},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.0,
        }
        start = time.time()
        response = self.session.post(endpoint, headers=headers, json=payload, timeout=90)
        elapsed_ms = int((time.time() - start) * 1000)

        if not response.ok:
            detail = _parse_api_error(response)
            hint = _hint_for_status(response.status_code, endpoint)
            msg = f"LLM API error {response.status_code} for model '{model}': {detail}"
            if hint:
                msg = f"{msg}\n\n{hint}"
            raise LlmApiError(
                msg,
                status_code=response.status_code,
                endpoint=endpoint,
                model=model,
                response_body=response.text[:1000],
            )

        body = _safe_response_json(response)
        if not body:
            snippet = (response.text or "")[:300]
            raise LlmApiError(
                "LLM returned a non-JSON response. "
                f"Check QWEN_ENDPOINT includes /chat/completions. Body preview: {snippet!r}",
                status_code=response.status_code,
                endpoint=endpoint,
                model=model,
                response_body=response.text[:1000],
            )
        text = (
            body.get("choices", [{}])[0]
            .get("message", {})
            .get("content", "")
        )
        return self._extract_sql(text), body, elapsed_ms

    def generate_sql(self, context_text: str) -> LlmResult:
        prompt = PROMPT_TEMPLATE.format(context=context_text)
        try:
            primary_sql, primary_body, latency = self._call_model(
                settings.qwen_endpoint, settings.qwen_api_key, settings.qwen_model, prompt
            )
        except LlmApiError:
            if not settings.v_api_key.strip() or settings.v_endpoint == settings.qwen_endpoint:
                raise
            primary_sql = ""

        if primary_sql.lower().startswith(("select", "with")):
            pt, ct, tt = _extract_token_usage(primary_body)
            return LlmResult(
                sql=primary_sql,
                model_used=settings.qwen_model,
                latency_ms=latency,
                used_fallback=False,
                raw_response=primary_body,
                prompt_tokens=pt,
                completion_tokens=ct,
                total_tokens=tt,
            )

        fallback_sql, fallback_body, fallback_latency = self._call_model(
            settings.v_endpoint, settings.v_api_key, settings.v_model, prompt
        )
        pt, ct, tt = _extract_token_usage(fallback_body)
        return LlmResult(
            sql=fallback_sql,
            model_used=settings.v_model,
            latency_ms=fallback_latency,
            used_fallback=True,
            raw_response=fallback_body,
            prompt_tokens=pt,
            completion_tokens=ct,
            total_tokens=tt,
        )

    @staticmethod
    def result_to_json(result: LlmResult) -> str:
        return json.dumps(
            {
                "model_used": result.model_used,
                "latency_ms": result.latency_ms,
                "used_fallback": result.used_fallback,
            },
            ensure_ascii=True,
        )
