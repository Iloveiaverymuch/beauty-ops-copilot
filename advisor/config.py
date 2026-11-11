"""Advisor (customer-facing chatbot) settings. Env-driven, same .env as the copilot."""
import os

PROJECT = os.getenv("GOOGLE_CLOUD_PROJECT", "")
BQ_DATASET = os.getenv("BQ_DATASET", "beauty_rag")
BQ_LOCATION = os.getenv("BQ_LOCATION", "EU")  # residency, consistent with Vertex `eu`
EMBED_MODEL = os.getenv("EMBED_MODEL", "gemini-embedding-001")  # multilingual (French corpus)
EMBED_LOCATION = os.getenv("EMBED_LOCATION", "europe-west1")  # set from scripts/probe_rag_models.py
EMBED_DIM = int(os.getenv("EMBED_DIM", "768"))  # MRL-truncated from 3072: 4x less storage, small quality loss
RANK_LOCATION = os.getenv("RANK_LOCATION", "global")
RANK_MODEL = os.getenv("RANK_MODEL", "semantic-ranker-default@latest")
