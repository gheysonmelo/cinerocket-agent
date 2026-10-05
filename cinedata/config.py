"""Configurações do agente, lidas de variáveis de ambiente (.env)."""

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

# Ordem de tentativa: o primeiro é o router oficial de modelos gratuitos;
# os demais são usados como fallback quando o provider está lotado (429 upstream).
DEFAULT_MODELS = [
    "openrouter/free",
    "nvidia/nemotron-3.5-lightning:free",
    "z-ai/glm-5.2:free",
    "google/gemma-4-26b-a4b-it:free",
]


def _resolve_db_path() -> Path:
    path = Path(os.getenv("CINEDATA_DB_PATH", "cinerocket.db"))
    return path if path.is_absolute() else (PROJECT_ROOT / path).resolve()


def _resolve_models() -> list[str]:
    raw = os.getenv("OPENROUTER_MODELS", "")
    models = [m.strip() for m in raw.split(",") if m.strip()]
    return models or DEFAULT_MODELS


@dataclass(frozen=True)
class Settings:
    api_key: str = field(default_factory=lambda: os.getenv("OPENROUTER_API_KEY", ""))
    base_url: str = "https://openrouter.ai/api/v1"
    models: list[str] = field(default_factory=_resolve_models)
    db_path: Path = field(default_factory=_resolve_db_path)
    max_rows: int = 50                # linhas devolvidas ao LLM por consulta
    query_timeout_s: float = 20.0     # aborta consultas muito pesadas
    max_tool_iterations: int = 6      # evita loops infinitos (e economiza cota)
    memory_turns: int = 6             # pares pergunta/resposta mantidos no histórico
    cache_dir: Path = PROJECT_ROOT / ".cache"
