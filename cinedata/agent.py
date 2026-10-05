"""Agente Text-to-SQL: loop de tool calling com fallback de modelos, memória e cache."""

import hashlib
import json
import logging
import unicodedata
from dataclasses import dataclass, field

from openai import APIConnectionError, APIStatusError, OpenAI

from .config import Settings
from .database import Database, QueryError, QueryResult
from .prompts import build_system_prompt

log = logging.getLogger(__name__)

EXECUTE_SQL_TOOL = {
    "type": "function",
    "function": {
        "name": "execute_sql",
        "description": (
            "Executa UMA consulta SQL de leitura (SELECT/WITH) no banco SQLite da CineData "
            "e retorna as linhas em JSON (máx. 50). Use sempre que precisar de dados."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Consulta SQLite somente-leitura."}
            },
            "required": ["query"],
        },
    },
}


class QuotaExceededError(RuntimeError):
    """Cota diária de modelos gratuitos do OpenRouter esgotada."""


class AllModelsFailedError(RuntimeError):
    """Nenhum modelo da lista de fallback conseguiu responder."""


@dataclass
class SQLStep:
    query: str
    result: QueryResult | None = None
    error: str | None = None


@dataclass
class AgentResponse:
    question: str
    answer: str
    model: str | None
    steps: list[SQLStep] = field(default_factory=list)
    from_cache: bool = False

    @property
    def last_result(self) -> QueryResult | None:
        """Resultado da última consulta bem-sucedida (útil para tabelas/gráficos)."""
        ok = [s.result for s in self.steps if s.result is not None]
        return ok[-1] if ok else None


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.lower()).encode("ascii", "ignore").decode()
    return " ".join(text.replace("?", " ").split())


class CineDataAgent:
    def __init__(self, settings: Settings | None = None, use_cache: bool = True):
        self.settings = settings or Settings()
        if not self.settings.api_key:
            raise ValueError("Defina OPENROUTER_API_KEY no arquivo .env")
        self.db = Database(
            self.settings.db_path, self.settings.max_rows, self.settings.query_timeout_s
        )
        # Sem retries automáticos do SDK: requisições que falham também contam na cota
        # diária, e quem trata a falha é o fallback para o próximo modelo.
        self.client = OpenAI(
            base_url=self.settings.base_url,
            api_key=self.settings.api_key,
            timeout=self.settings.llm_timeout_s,
            max_retries=0,
        )
        self.system_prompt = build_system_prompt(self.db.schema_ddl())
        self.history: list[dict] = []
        self.use_cache = use_cache
        self._cache_file = self.settings.cache_dir / "answers.json"
        self._cache = self._load_cache()

    # ------------------------------------------------------------------ API pública
    def ask(self, question: str) -> AgentResponse:
        """Responde uma pergunta em linguagem natural, mantendo o contexto da conversa."""
        cache_key = self._cache_key(question)
        if self.use_cache and not self.history and cache_key in self._cache:
            cached = self._cache[cache_key]
            response = AgentResponse(
                question, cached["answer"], cached["model"],
                [SQLStep(q) for q in cached["queries"]], from_cache=True,
            )
            self._remember(question, response.answer)
            return response

        messages = [
            {"role": "system", "content": self.system_prompt},
            *self.history,
            {"role": "user", "content": question},
        ]
        steps: list[SQLStep] = []
        model_used = None

        for _ in range(self.settings.max_tool_iterations):
            message, model_used = self._complete(messages)
            if not message.tool_calls:
                answer = (message.content or "").strip() or "Não consegui gerar uma resposta."
                break

            messages.append(message.model_dump(exclude_none=True))
            for call in message.tool_calls:
                messages.append(
                    {"role": "tool", "tool_call_id": call.id, "content": self._run_tool(call, steps)}
                )
        else:
            answer = "Atingi o limite de etapas sem concluir a análise. Tente reformular a pergunta."

        response = AgentResponse(question, answer, model_used, steps)
        self._remember(question, answer)
        if self.use_cache and len(self.history) == 2 and any(s.result for s in steps):
            self._save_to_cache(cache_key, response)
        return response

    def reset(self) -> None:
        """Limpa a memória da conversa."""
        self.history.clear()

    # ------------------------------------------------------------------ LLM
    def _complete(self, messages: list[dict]):
        """Chama os modelos em ordem; passa para o próximo se o provider falhar."""
        errors = []
        for model in self.settings.models:
            try:
                resp = self.client.chat.completions.create(
                    model=model, messages=messages, tools=[EXECUTE_SQL_TOOL],
                    tool_choice="auto", temperature=0,
                )
                if not resp.choices:
                    raise ValueError(getattr(resp, "error", None) or "resposta vazia")
                return resp.choices[0].message, model
            except APIStatusError as exc:
                if exc.status_code == 429 and "per-day" in str(exc).lower():
                    raise QuotaExceededError(
                        "Cota diária de modelos gratuitos atingida (reseta às 21h BRT)."
                    ) from exc
                if exc.status_code in (401, 402):
                    raise
                errors.append(f"{model}: HTTP {exc.status_code}")
            except (APIConnectionError, ValueError) as exc:
                errors.append(f"{model}: {exc}")
            log.warning("Falha no modelo %s, tentando o próximo", model)
        raise AllModelsFailedError("Nenhum modelo respondeu -> " + "; ".join(errors))

    # ------------------------------------------------------------------ Tools
    def _run_tool(self, call, steps: list[SQLStep]) -> str:
        if call.function.name != "execute_sql":
            return json.dumps({"error": f"Ferramenta desconhecida: {call.function.name}"})
        try:
            query = json.loads(call.function.arguments or "{}").get("query", "")
        except json.JSONDecodeError:
            return json.dumps({"error": "Argumentos inválidos: envie JSON com a chave 'query'."})

        step = SQLStep(query)
        steps.append(step)
        try:
            step.result = self.db.run_query(query)
        except QueryError as exc:
            step.error = str(exc)
            return json.dumps({"error": step.error}, ensure_ascii=False)

        payload = {"columns": step.result.columns, "rows": step.result.rows}
        if step.result.truncated:
            payload["aviso"] = f"Resultado truncado em {self.settings.max_rows} linhas."
        return json.dumps(payload, ensure_ascii=False, default=str)

    # ------------------------------------------------------------------ Memória e cache
    def _remember(self, question: str, answer: str) -> None:
        self.history += [
            {"role": "user", "content": question},
            {"role": "assistant", "content": answer},
        ]
        self.history = self.history[-2 * self.settings.memory_turns:]

    def _cache_key(self, question: str) -> str:
        return hashlib.sha256(_normalize(question).encode()).hexdigest()[:16]

    def _load_cache(self) -> dict:
        try:
            return json.loads(self._cache_file.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def _save_to_cache(self, key: str, response: AgentResponse) -> None:
        self._cache[key] = {
            "question": response.question,
            "answer": response.answer,
            "model": response.model,
            "queries": [s.query for s in response.steps if s.result is not None],
        }
        self._cache_file.parent.mkdir(exist_ok=True)
        self._cache_file.write_text(
            json.dumps(self._cache, ensure_ascii=False, indent=2), encoding="utf-8"
        )
