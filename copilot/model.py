"""One place to build the LLM client: model id, per-attempt timeout, retry policy.

Why: on the `eu` endpoint we saw 503 "upstream connect error" and single requests hanging >240 s.
A per-attempt timeout turns a hang into a fast retry; retries cover transient 429/5xx.
Budget: worst case ~= attempts x timeout, so keep it under the caller's own deadline.
"""
import os
from pathlib import Path
from dotenv import load_dotenv

_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_ROOT / ".env")
load_dotenv(_ROOT / "copilot" / ".env")
if "GOOGLE_GENAI_USE_VERTEXAI" not in os.environ and "GEMINI_API_KEY" not in os.environ:
    os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "TRUE"

from google.adk.models import Gemini
from google.genai import types


def build_model() -> Gemini:
    timeout_s = float(os.getenv("MODEL_TIMEOUT_S", "60"))
    return Gemini(
        model=os.getenv("MODEL", "gemini-3.8-flash"),
        retry_options=types.HttpRetryOptions(
            attempts=int(os.getenv("MODEL_RETRY_ATTEMPTS", "3")),
            initial_delay=1.0, max_delay=8.0, exp_base=2.0, jitter=0.5,
            http_status_codes=[408, 429, 500, 502, 503, 504],
        ),
        client_kwargs={"http_options": types.HttpOptions(timeout=int(timeout_s * 1000))},  # ms
    )


def build_generate_config(role: str | None = None) -> types.GenerateContentConfig | None:
    """Thinking level per agent ROLE: THINKING_LEVEL_<ROLE> (ROUTER|CATALOG|WORKSPACE|M365), falling back to THINKING_LEVEL.
    Values: MINIMAL|LOW|MEDIUM|HIGH (Gemini 3); unset/empty = model default."""
    role_key = role.upper() if role else ""
    level = None
    if role_key:
        level = os.getenv(f"THINKING_LEVEL_{role_key}")
        if not level and role_key in ("WORKSPACE", "M365"):
            level = os.getenv("THINKING_LEVEL_WORKSPACE") or os.getenv("THINKING_LEVEL_M365")
    level = level or os.getenv("THINKING_LEVEL")
    if not level:
        return None
    return types.GenerateContentConfig(
        thinking_config=types.ThinkingConfig(thinking_level=types.ThinkingLevel[level.upper()]))
