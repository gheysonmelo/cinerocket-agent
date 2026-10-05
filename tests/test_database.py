"""Testes dos guardrails de leitura (não consomem cota do OpenRouter)."""

import pytest

from cinedata.config import Settings
from cinedata.database import Database, QueryError

settings = Settings()
pytestmark = pytest.mark.skipif(not settings.db_path.exists(), reason="cinerocket.db ausente")


@pytest.fixture(scope="module")
def db():
    return Database(settings.db_path, max_rows=5)


def test_select_simples(db):
    result = db.run_query("SELECT nome_genero FROM dim_genres ORDER BY nome_genero")
    assert result.columns == ["nome_genero"]
    assert len(result.rows) == 5 and result.truncated


def test_coluna_com_palavra_reservada_no_nome_e_permitida(db):
    # 'created_at' contém CREATE; um filtro por palavra-chave bloquearia isso.
    result = db.run_query("SELECT created_at FROM movie_reviews LIMIT 1")
    assert result.rows


def test_cte_e_permitida(db):
    result = db.run_query("WITH g AS (SELECT * FROM dim_genres) SELECT COUNT(*) FROM g")
    assert result.rows[0][0] == 19


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM dim_genres",
        "UPDATE dim_genres SET nome_genero = 'x'",
        "DROP TABLE dim_genres",
        "INSERT INTO dim_genres VALUES ('a', 'b')",
        "PRAGMA table_info(dim_genres)",
        "ATTACH DATABASE 'x.db' AS x",
        "SELECT 1; DROP TABLE dim_genres",
        "WITH x AS (SELECT 1) DELETE FROM dim_genres",
    ],
)
def test_escrita_e_bloqueada(db, sql):
    with pytest.raises(QueryError):
        db.run_query(sql)


def test_sql_invalido_retorna_erro_legivel(db):
    with pytest.raises(QueryError, match="no such column"):
        db.run_query("SELECT coluna_inexistente FROM dim_movies")


def test_schema_nao_expoe_tabela_interna(db):
    ddl = db.schema_ddl()
    assert "dim_movies" in ddl and "alembic_version" not in ddl
