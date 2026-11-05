"""One place to build the LLM client: model id, per-attempt timeout, retry policy.

Why: on the `eu` endpoint we saw 503 "upstream connect error" and single requests hanging >240 s.
A per-attempt timeout turns a hang into a fast retry; retries cover transient 429/5xx.
Budget: worst case ~= attempts x timeout, so keep it under the caller's own deadline.
"""
import os

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
