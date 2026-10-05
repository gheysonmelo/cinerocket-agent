"""Chat no terminal com o agente CineData.

Uso:
    python main.py                      # modo interativo
    python main.py "sua pergunta aqui"  # pergunta única
"""

import sys

from cinedata.agent import AllModelsFailedError, CineDataAgent, QuotaExceededError

HELP = "Comandos: /sql (mostrar/ocultar SQL), /limpar (zera a memória), /sair"


def print_response(response, show_sql: bool) -> None:
    if show_sql:
        for i, step in enumerate(response.steps, 1):
            status = f"ERRO: {step.error}" if step.error else "ok"
            print(f"\n[SQL {i} - {status}]\n{step.query}")
    origem = "cache" if response.from_cache else response.model
    print(f"\n{response.answer}\n\n(fonte: {origem})")


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    agent = CineDataAgent()

    if len(sys.argv) > 1:
        print_response(agent.ask(" ".join(sys.argv[1:])), show_sql=True)
        return

    print("🎬 CineData Analytics — pergunte sobre o catálogo de filmes.\n" + HELP)
    show_sql = True
    while True:
        try:
            question = input("\nVocê: ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not question:
            continue
        if question == "/sair":
            break
        if question == "/sql":
            show_sql = not show_sql
            print(f"Exibição de SQL {'ativada' if show_sql else 'desativada'}.")
            continue
        if question == "/limpar":
            agent.reset()
            print("Memória da conversa apagada.")
            continue

        try:
            print_response(agent.ask(question), show_sql)
        except QuotaExceededError as exc:
            print(f"\n⚠️ {exc}")
            break
        except AllModelsFailedError as exc:
            print(f"\n⚠️ {exc}\nTente novamente em alguns instantes.")


if __name__ == "__main__":
    main()
