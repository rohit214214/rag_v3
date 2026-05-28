"""Quick DashScope connectivity test. Run from repo root: python scripts/test_dashscope.py"""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from config.cf import load_config, loaded_dotenv_path
from config.settings import settings
from services.llm_client import LlmApiError, LlmClient

load_config()
env_path = loaded_dotenv_path()

print("Env file:", env_path)
if env_path and env_path.name == ".env.example":
    print(
        "WARNING: Using .env.example. Copy to cf.env (recommended) or .env with your real API key."
    )
print("Endpoint:", settings.qwen_endpoint)
print("Model:", settings.qwen_model)
print("API key:", "set" if settings.qwen_api_key else "MISSING")

if not settings.qwen_endpoint.endswith("/chat/completions"):
    print("ERROR: Endpoint must end with /chat/completions")
    raise SystemExit(1)

client = LlmClient()
try:
    result = client._call_model(
        settings.qwen_endpoint,
        settings.qwen_api_key,
        settings.qwen_model,
        "Reply with only: SELECT 1",
    )
    print("OK:", result[0][:200])
except LlmApiError as exc:
    print("FAILED:", exc)
    raise SystemExit(1) from exc
