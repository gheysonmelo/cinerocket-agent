# 🎬 CineData Analytics — Agente Text-to-SQL

Agente que permite a usuários **não técnicos** fazer perguntas em linguagem natural sobre o catálogo de filmes da CineData Analytics. Ele converte a pergunta em SQL, consulta a **camada Gold** (SQLite) em tempo real e responde em português.

> Atividade GenAI — Rocket Lab 2026 (Visagio)

## Stack

| Item | Escolha |
|---|---|
| Linguagem | Python 3.10+ |
| Framework de agente | Implementação própria com tool calling (SDK `openai`), sem frameworks pesados |
| Modelos | OpenRouter, gratuitos: `openrouter/free` com fallback para `nvidia/nemotron-3.5-lightning:free`, `z-ai/glm-5.2:free` e `google/gemma-4-26b-a4b-it:free` |
| Banco | SQLite3 (`cinerocket.db`), 10 tabelas do modelo dimensional |
| Entregável | Pacote Python + Jupyter Notebook + chat no terminal |

## Como funciona

```
Pergunta ──► LLM (schema + regras de negócio no prompt)
                │  tool call: execute_sql(query)
                ▼
          Guardrails ──► SQLite (read-only) ──► linhas (máx. 50)
                │
                ▼
          LLM interpreta (ou corrige a query se deu erro) ──► Resposta em PT-BR
```

### Funcionalidades

- **Text-to-SQL com auto-correção:** se a query falhar, o erro do banco volta para o LLM, que corrige e tenta de novo (limite de 6 iterações).
- **Guardrails de leitura:**
  - a conexão abre em modo `read-only`;
  - um autorizador do próprio SQLite libera apenas `SELECT`, então `INSERT`/`UPDATE`/`DELETE`/`DROP`/`PRAGMA`/`ATTACH` são negados pelo motor;
  - cada execução aceita um único statement;
  - consultas têm timeout e limite de linhas.
- **Regras de negócio no prompt:**
  - os sinônimos receita = faturamento = bilheteria;
  - `lucro_*` vale 0 quando a receita não foi informada, então o agente filtra esses casos;
  - os valores válidos de `tipo_pessoa` (`Ator`/`Diretor`/`Roteirista`);
  - a data atual, para filtros como "últimos 5 anos".
- **Fallback entre modelos gratuitos:** se um provider estiver lotado (429 upstream), tenta o próximo. Se a cota **diária** acabou, para na hora, sem queimar requisições.
- **Memória de conversa:** perguntas de acompanhamento ("e a nota média deles?") funcionam.
- **Cache de respostas** em `.cache/answers.json`: perguntas repetidas não gastam cota.

## Passo a passo para executar

### 1. Clonar o repositório

```bash
git clone https://github.com/gheysonmelo/cinerocket-agent.git
cd cinerocket-agent
```

### 2. (Opcional) Criar um ambiente virtual

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows
source .venv/bin/activate     # Linux/macOS
```

### 3. Instalar as dependências

```bash
pip install -r requirements.txt
```

### 4. Configurar a chave do OpenRouter

1. Crie uma conta em [openrouter.ai](https://openrouter.ai) e gere uma chave em [openrouter.ai/keys](https://openrouter.ai/keys).
2. Copie o arquivo de exemplo e preencha a chave:

```bash
cp .env.example .env
```

```env
OPENROUTER_API_KEY=sk-or-v1-...
CINEDATA_DB_PATH=cinerocket.db
```

### 5. Adicionar o banco de dados

Baixe o `cinerocket.db` (pasta compartilhada da atividade) e coloque-o na raiz do projeto. Se ele estiver em outro lugar, aponte o caminho em `CINEDATA_DB_PATH` no `.env`. O arquivo **não** é versionado (~580 MB).

### 6. Executar

**Notebook** (demonstração com todas as perguntas da atividade):

```bash
jupyter notebook agente_cinedata.ipynb
```

**Chat no terminal:**

```bash
python main.py
```

**Pergunta única:**

```bash
python main.py "Qual produtora teve o maior lucro total?"
```

No chat, use `/sql` para mostrar/ocultar o SQL gerado, `/limpar` para zerar a memória e `/sair` para encerrar.

**Uso como biblioteca:**

```python
from cinedata.agent import CineDataAgent

agent = CineDataAgent()
r = agent.ask("Quais são os 5 filmes mais populares?")
print(r.answer)            # resposta em linguagem natural
print(r.steps[-1].query)   # SQL executado
```

### 7. Testes (não consomem cota)

```bash
python -m pytest -q
```

## Estrutura do projeto

```
cinerocket-agent/
├── cinedata/
│   ├── config.py      # Variáveis de ambiente e limites
│   ├── database.py    # Conexão read-only + guardrails + schema
│   ├── prompts.py     # Prompt de sistema e regras de negócio
│   └── agent.py       # Loop de tool calling, fallback, memória e cache
├── tests/
│   └── test_database.py
├── agente_cinedata.ipynb
├── main.py            # Chat no terminal
├── requirements.txt
└── .env.example
```

## Exemplos de perguntas

| Categoria | Pergunta |
|---|---|
| Bilheteria e finanças | Quais são os 10 filmes com maior receita em R$? |
| Popularidade | Quais filmes têm maior divergência entre a nota TMDB e a nota IMDb? |
| Elenco e equipe | Qual dupla ator-diretor mais trabalhou junta? |
| Gêneros e produtoras | Qual gênero tem a maior margem de lucro média? |
| Avaliações | Em quais filmes a nota média dos usuários mais diverge da nota IMDb? |

## Limitações

- O plano gratuito do OpenRouter tem **50 requisições/dia** (reseta às 21h BRT). Cada pergunta usa em média 2 requisições.
- Modelos gratuitos podem ficar lotados em horários de pico. O fallback ajuda, mas não garante resposta.
- Só 3.373 dos ~95 mil filmes têm receita informada, então análises financeiras cobrem esse subconjunto.
