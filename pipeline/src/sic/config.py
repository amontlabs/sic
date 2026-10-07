from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[3]  # repo root
DATA = ROOT / "data"
CACHE = DATA / "cache"
RUNS = DATA / "runs"
DEBATES = DATA / "debates"
MODELS_DIR = DATA / "models"

load_dotenv(ROOT / ".env")

ZAI_API_KEY = os.environ.get("ZAI_API_KEY", "")
ZAI_BASE_URL = os.environ.get("ZAI_BASE_URL", "https://api.z.ai/api/coding/paas/v4")
ZAI_MCP_URL = os.environ.get("ZAI_MCP_URL", "https://api.z.ai/api/mcp")

LLM_FAST = os.environ.get("SIC_LLM_FAST", "glm-5.3-flash")
LLM_SMART = os.environ.get("SIC_LLM_SMART", "glm-5.3")

# Let the verifier reason before answering: fewer critical errors on the 2022 eval, similar latency.
VERIFY_THINKING = os.environ.get("SIC_VERIFY_THINKING", "enabled")

ASR_MODEL = os.environ.get("SIC_ASR_MODEL", "mlx-community/whisper-large-v3-turbo")
