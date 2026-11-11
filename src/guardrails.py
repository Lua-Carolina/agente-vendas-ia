"""Validacao de SQL: apenas leitura, com limite de linhas e de tempo.

Este modulo e a unica porta de entrada para SQL escrito livremente pelo LLM.
A regra e simples: se a consulta nao for comprovadamente uma leitura, ela nao roda.
"""

from __future__ import annotations

import re
import threading

LIMITE_LINHAS = 1000
TIMEOUT_SEGUNDOS = 5.0

# Comandos que escrevem, mudam o schema, acessam rede/disco ou adminstram o banco.
COMANDOS_PROIBIDOS = (
    "insert", "update", "delete", "drop", "alter", "create", "truncate",
    "attach", "detach", "copy", "export", "import", "install", "load",
    "pragma", "set", "reset", "call", "execute", "prepare", "vacuum",
    "grant", "revoke", "merge", "replace", "comment", "checkpoint",
)

# Funcoes que leem o ambiente ou o disco fora do banco.
FUNCOES_PROIBIDAS = (
    "read_csv", "read_csv_auto", "read_parquet", "read_json", "read_json_auto",
    "read_text", "read_blob", "glob", "getenv", "current_setting", "shell",
)


class SQLInvalidoError(ValueError):
    """Levantado quando uma consulta viola as regras de seguranca."""


def _mascarar_literais(sql: str) -> str:
    """Troca comentarios e textos entre aspas por espacos.

    Sem isso, uma coluna chamada 'update' ou um filtro WHERE canal = 'delivery drop'
    seriam confundidos com comandos proibidos.
    """
    sem_comentario = re.sub(r"--[^\n]*", " ", sql)
    sem_comentario = re.sub(r"/\*.*?\*/", " ", sem_comentario, flags=re.DOTALL)
    sem_literal = re.sub(r"'(?:''|[^'])*'", " '' ", sem_comentario)
    sem_identificador = re.sub(r'"(?:""|[^"])*"', ' "" ', sem_literal)
    return sem_identificador


def validar_sql(sql: str) -> str:
    """Valida a consulta e devolve o SQL limpo, pronto para execucao.

    Regras:
      - precisa ser uma unica instrucao;
      - precisa comecar com SELECT ou WITH;
      - nao pode conter comandos de escrita, DDL, anexo de arquivos ou funcoes
        que leem o sistema de arquivos.
    """
    if not sql or not sql.strip():
        raise SQLInvalidoError("A consulta esta vazia.")

    limpo = sql.strip().rstrip(";").strip()
    if not limpo:
        raise SQLInvalidoError("A consulta esta vazia.")

    # Uma instrucao por vez: sem encadear consultas com ponto e virgula.
    if ";" in limpo:
        raise SQLInvalidoError("Envie apenas uma instrucao por consulta (sem ';' no meio).")

    mascarado = _mascarar_literais(limpo).lower()

    if not re.match(r"^\s*(select|with)\b", mascarado):
        raise SQLInvalidoError("Apenas consultas de leitura (SELECT ou WITH) sao permitidas.")

    for comando in COMANDOS_PROIBIDOS:
        if re.search(rf"\b{comando}\b", mascarado):
            raise SQLInvalidoError(f"Comando nao permitido: {comando.upper()}.")

    for funcao in FUNCOES_PROIBIDAS:
        if re.search(rf"\b{funcao}\s*\(", mascarado):
            raise SQLInvalidoError(f"Funcao nao permitida: {funcao}.")

    return limpo


def aplicar_limite(sql: str, limite: int = LIMITE_LINHAS) -> str:
    """Envolve a consulta em um SELECT externo com LIMIT.

    Envolver (em vez de procurar um LIMIT no texto) garante o teto mesmo quando
    a consulta ja tem LIMIT, UNION ou ORDER BY.
    """
    return f"SELECT * FROM (\n{sql}\n) AS resultado_guardrail LIMIT {int(limite)}"


def executar_sql_seguro(
    conexao,  # duckdb.DuckDBPyConnection, sem import aqui para nao acoplar
    sql: str,
    limite: int = LIMITE_LINHAS,
    timeout: float = TIMEOUT_SEGUNDOS,
):
    """Valida, aplica o limite de linhas e executa com timeout.

    O timeout e implementado com conexao.interrupt() em uma thread: se a consulta
    passar do tempo, o DuckDB aborta e levanta uma excecao.
    """
    validado = validar_sql(sql)
    consulta = aplicar_limite(validado, limite)

    disparou = threading.Event()

    def _interromper() -> None:
        disparou.set()
        conexao.interrupt()

    alarme = threading.Timer(timeout, _interromper)
    alarme.daemon = True
    alarme.start()
    try:
        resultado = conexao.execute(consulta).df()
    except Exception as erro:
        if disparou.is_set():
            raise TimeoutError(
                f"A consulta passou do limite de {timeout:g}s e foi cancelada."
            ) from erro
        raise
    finally:
        alarme.cancel()

    return resultado
