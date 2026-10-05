"""Acesso somente-leitura à camada Gold (SQLite) com guardrails."""

import re
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

# Ações que o autorizador do SQLite permite. Qualquer outra (INSERT, UPDATE,
# DELETE, CREATE, DROP, ATTACH, PRAGMA...) é negada pelo próprio motor do banco,
# o que é mais robusto do que procurar palavras-chave no texto da query.
_ALLOWED_ACTIONS = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION}
if hasattr(sqlite3, "SQLITE_RECURSIVE"):
    _ALLOWED_ACTIONS.add(sqlite3.SQLITE_RECURSIVE)

_HIDDEN_TABLES = {"alembic_version"}
_READ_PREFIX = re.compile(r"^\s*(SELECT|WITH)\b", re.IGNORECASE)


class QueryError(Exception):
    """Erro de validação ou execução de uma consulta."""


@dataclass
class QueryResult:
    columns: list[str]
    rows: list[tuple]
    truncated: bool
    elapsed_s: float

    def as_records(self) -> list[dict]:
        return [dict(zip(self.columns, row)) for row in self.rows]


def _authorizer(action, *_):
    return sqlite3.SQLITE_OK if action in _ALLOWED_ACTIONS else sqlite3.SQLITE_DENY


class Database:
    def __init__(self, db_path: Path, max_rows: int = 50, timeout_s: float = 20.0):
        if not Path(db_path).exists():
            raise FileNotFoundError(
                f"Banco não encontrado em '{db_path}'. Copie o cinerocket.db para a pasta "
                "do projeto ou ajuste CINEDATA_DB_PATH no .env."
            )
        self.db_path = Path(db_path)
        self.max_rows = max_rows
        self.timeout_s = timeout_s

    def _connect(self) -> sqlite3.Connection:
        # mode=ro: mesmo que o autorizador falhasse, o arquivo é aberto só para leitura.
        uri = f"{self.db_path.resolve().as_uri()}?mode=ro"
        return sqlite3.connect(uri, uri=True)

    def run_query(self, sql: str) -> QueryResult:
        sql = sql.strip().rstrip(";")
        if not _READ_PREFIX.match(sql):
            raise QueryError("Apenas consultas de leitura (SELECT/WITH) são permitidas.")

        conn = self._connect()
        conn.set_authorizer(_authorizer)
        deadline = time.monotonic() + self.timeout_s
        # Retornar valor não-zero interrompe a consulta.
        conn.set_progress_handler(lambda: int(time.monotonic() > deadline), 10_000)
        start = time.monotonic()
        try:
            cur = conn.execute(sql)  # sqlite3 recusa múltiplos statements
            columns = [d[0] for d in cur.description or []]
            rows = cur.fetchmany(self.max_rows + 1)
        except sqlite3.DatabaseError as exc:
            msg = str(exc)
            if "interrupted" in msg:
                msg = f"Consulta excedeu {self.timeout_s:.0f}s. Simplifique ou agregue mais."
            elif "not authorized" in msg:
                msg = "Operação não permitida: o agente só pode ler dados."
            raise QueryError(msg) from exc
        except sqlite3.Warning as exc:
            raise QueryError(str(exc)) from exc
        finally:
            conn.close()

        truncated = len(rows) > self.max_rows
        return QueryResult(columns, rows[: self.max_rows], truncated, time.monotonic() - start)

    def schema_ddl(self) -> str:
        """DDL das tabelas (sem constraints) para compor o prompt do LLM."""
        conn = self._connect()
        try:
            tables = [
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
                )
                if r[0] not in _HIDDEN_TABLES
            ]
            parts = []
            for table in tables:
                cols = conn.execute(f"PRAGMA table_info({table})").fetchall()
                col_defs = ",\n  ".join(f"{c[1]} {c[2]}" for c in cols)
                parts.append(f"CREATE TABLE {table} (\n  {col_defs}\n);")
            return "\n".join(parts)
        finally:
            conn.close()
