"""Ferramentas de consulta do agente, todas somente leitura.

Cada funcao e uma pergunta de negocio pronta: o LLM escolhe qual chamar e com
quais parametros, mas nunca escreve SQL por conta propria (exceto na ferramenta
`consultar_sql`, que e a ultima alternativa e passa por guardrails).
"""

from __future__ import annotations

import atexit
import calendar
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path

import duckdb
import pandas as pd

from src.guardrails import executar_sql_seguro

CAMINHO_BANCO = Path(__file__).resolve().parent.parent / "data" / "vendas.duckdb"

# Periodos nomeados aceitos pelas ferramentas.
PERIODOS = (
    "hoje",
    "ontem",
    "ultimos_7_dias",
    "ultimos_30_dias",
    "mes_atual",
    "mes_anterior",
    "ultimos_6_meses",
    "tudo",
)

GRANULARIDADES = {"dia": "day", "semana": "week", "mes": "month"}


class BancoIndisponivelError(RuntimeError):
    """Levantado quando o arquivo DuckDB ainda nao foi gerado."""


def _fechar(conexao: duckdb.DuckDBPyConnection) -> None:
    """Fecha a conexao no encerramento do processo (evita ruido do DuckDB)."""
    try:
        conexao.close()
    except Exception:
        pass


@lru_cache(maxsize=1)
def conectar() -> duckdb.DuckDBPyConnection:
    """Abre (uma unica vez) a conexao em modo somente leitura."""
    if not CAMINHO_BANCO.exists():
        raise BancoIndisponivelError(
            "Banco de dados nao encontrado. Rode: python data/gerar_dados.py"
        )
    conexao = duckdb.connect(str(CAMINHO_BANCO), read_only=True)
    atexit.register(_fechar, conexao)
    return conexao


def intervalo(
    periodo: str = "ultimos_30_dias",
    inicio: str | None = None,
    fim: str | None = None,
) -> tuple[date, date]:
    """Traduz um periodo nomeado (ou datas explicitas) em (inicio, fim)."""
    hoje = date.today()

    if inicio or fim:
        if not (inicio and fim):
            raise ValueError("Informe inicio e fim juntos, no formato AAAA-MM-DD.")
        ini, f = date.fromisoformat(inicio), date.fromisoformat(fim)
        if ini > f:
            raise ValueError("A data de inicio precisa ser anterior a data de fim.")
        return ini, f

    if periodo == "hoje":
        return hoje, hoje
    if periodo == "ontem":
        ontem = hoje - timedelta(days=1)
        return ontem, ontem
    if periodo == "ultimos_7_dias":
        return hoje - timedelta(days=6), hoje
    if periodo == "ultimos_30_dias":
        return hoje - timedelta(days=29), hoje
    if periodo == "mes_atual":
        return hoje.replace(day=1), hoje
    if periodo == "mes_anterior":
        primeiro_do_mes = hoje.replace(day=1)
        ultimo = primeiro_do_mes - timedelta(days=1)
        return ultimo.replace(day=1), ultimo
    if periodo in ("ultimos_6_meses", "tudo"):
        return hoje - timedelta(days=179), hoje

    raise ValueError(
        f"Periodo desconhecido: {periodo!r}. Use um destes: {', '.join(PERIODOS)} "
        "ou informe inicio e fim em AAAA-MM-DD."
    )


def _consulta_df(sql: str, parametros: list) -> pd.DataFrame:
    """Executa SQL parametrizado (somente leitura) e devolve um DataFrame."""
    return conectar().execute(sql, parametros).df()


def vendas_por_periodo(
    periodo: str = "ultimos_30_dias",
    agrupar_por: str = "dia",
    inicio: str | None = None,
    fim: str | None = None,
) -> pd.DataFrame:
    """Receita e numero de pedidos ao longo do tempo.

    Args:
        periodo: janela nomeada (ver PERIODOS).
        agrupar_por: 'dia', 'semana' ou 'mes'.
        inicio: data inicial AAAA-MM-DD (opcional, substitui o periodo).
        fim: data final AAAA-MM-DD (opcional).
    """
    unidade = GRANULARIDADES.get(agrupar_por)
    if unidade is None:
        raise ValueError(f"agrupar_por invalido: {agrupar_por!r}. Use dia, semana ou mes.")

    ini, f = intervalo(periodo, inicio, fim)
    sql = f"""
        SELECT
            date_trunc('{unidade}', data_hora)::DATE AS periodo,
            count(*)::BIGINT AS pedidos,
            round(sum(total), 2)::DOUBLE AS receita
        FROM pedidos
        WHERE data_hora::DATE BETWEEN ? AND ?
        GROUP BY 1
        ORDER BY 1
    """
    return _consulta_df(sql, [ini, f])


ORDENACOES = {
    "quantidade": "quantidade DESC, receita DESC",
    "receita": "receita DESC, quantidade DESC",
}


def top_itens(
    periodo: str = "ultimos_30_dias",
    limite: int = 5,
    ordenar_por: str = "quantidade",
    inicio: str | None = None,
    fim: str | None = None,
) -> pd.DataFrame:
    """Itens mais vendidos no periodo, por quantidade ou por receita."""
    criterio = ORDENACOES.get(ordenar_por)
    if criterio is None:
        raise ValueError("ordenar_por invalido: use 'quantidade' ou 'receita'.")

    ini, f = intervalo(periodo, inicio, fim)
    sql = f"""
        SELECT
            i.nome,
            i.categoria,
            sum(ip.quantidade)::BIGINT AS quantidade,
            round(sum(ip.quantidade * ip.preco_unitario), 2)::DOUBLE AS receita
        FROM itens_pedido ip
        JOIN itens i ON i.id = ip.item_id
        JOIN pedidos p ON p.id = ip.pedido_id
        WHERE p.data_hora::DATE BETWEEN ? AND ?
        GROUP BY i.nome, i.categoria
        ORDER BY {criterio}
        LIMIT ?
    """
    return _consulta_df(sql, [ini, f, max(1, min(int(limite), 50))])


def piores_itens(
    periodo: str = "ultimos_30_dias",
    limite: int = 5,
    inicio: str | None = None,
    fim: str | None = None,
) -> pd.DataFrame:
    """Itens menos vendidos no periodo (candidatos a sair do menu).

    Itens sem nenhuma venda no periodo tambem aparecem, com quantidade 0.
    """
    ini, f = intervalo(periodo, inicio, fim)
    sql = """
        WITH vendas AS (
            SELECT i.id, sum(ip.quantidade)::BIGINT AS quantidade,
                   sum(ip.quantidade * ip.preco_unitario) AS receita
            FROM itens_pedido ip
            JOIN itens i ON i.id = ip.item_id
            JOIN pedidos p ON p.id = ip.pedido_id
            WHERE p.data_hora::DATE BETWEEN ? AND ?
            GROUP BY i.id
        )
        SELECT
            i.nome,
            i.categoria,
            coalesce(v.quantidade, 0) AS quantidade,
            round(coalesce(v.receita, 0), 2)::DOUBLE AS receita
        FROM itens i
        LEFT JOIN vendas v ON v.id = i.id
        ORDER BY quantidade ASC, receita ASC
        LIMIT ?
    """
    return _consulta_df(sql, [ini, f, max(1, min(int(limite), 50))])


def ticket_medio(
    periodo: str = "ultimos_30_dias",
    inicio: str | None = None,
    fim: str | None = None,
) -> dict:
    """Total de pedidos, receita e ticket medio no periodo."""
    ini, f = intervalo(periodo, inicio, fim)
    sql = """
        SELECT
            count(*)::BIGINT AS pedidos,
            round(coalesce(sum(total), 0), 2)::DOUBLE AS receita_total,
            round(coalesce(avg(total), 0), 2)::DOUBLE AS ticket_medio
        FROM pedidos
        WHERE data_hora::DATE BETWEEN ? AND ?
    """
    linha = _consulta_df(sql, [ini, f]).iloc[0]
    return {
        "inicio": ini.isoformat(),
        "fim": f.isoformat(),
        "pedidos": int(linha["pedidos"]),
        "receita_total": float(linha["receita_total"]),
        "ticket_medio": float(linha["ticket_medio"]),
    }


def vendas_por_horario(
    periodo: str = "ultimos_30_dias",
    inicio: str | None = None,
    fim: str | None = None,
) -> pd.DataFrame:
    """Distribuicao de pedidos por hora do dia, com a faixa correspondente."""
    ini, f = intervalo(periodo, inicio, fim)
    sql = """
        SELECT
            extract(hour FROM data_hora)::INTEGER AS hora,
            CASE
                WHEN extract(hour FROM data_hora) BETWEEN 11 AND 14 THEN 'almoco'
                WHEN extract(hour FROM data_hora) BETWEEN 15 AND 17 THEN 'tarde'
                WHEN extract(hour FROM data_hora) BETWEEN 18 AND 23 THEN 'jantar'
                ELSE 'madrugada'
            END AS faixa,
            count(*)::BIGINT AS pedidos,
            round(sum(total), 2)::DOUBLE AS receita
        FROM pedidos
        WHERE data_hora::DATE BETWEEN ? AND ?
        GROUP BY 1, 2
        ORDER BY 1
    """
    return _consulta_df(sql, [ini, f])


def consultar_sql(consulta: str) -> pd.DataFrame:
    """Executa SQL livre somente leitura, passando pelos guardrails.

    Ultima alternativa: prefira as ferramentas prontas. A consulta e validada
    (apenas SELECT/WITH), limitada a 1000 linhas e a 5 segundos.
    """
    return executar_sql_seguro(conectar(), consulta)


# ---------------------------------------------------------------------------
# Catalogo de ferramentas exposto ao LLM (formato OpenAI function calling).
# ---------------------------------------------------------------------------
_PARAMETROS_PERIODO = {
    "periodo": {
        "type": "string",
        "enum": list(PERIODOS),
        "description": "Janela de tempo. Padrao: ultimos_30_dias.",
    },
    "inicio": {
        "type": "string",
        "description": "Data inicial AAAA-MM-DD. Use com 'fim' para periodo explicito.",
    },
    "fim": {
        "type": "string",
        "description": "Data final AAAA-MM-DD. Use com 'inicio'.",
    },
}

FERRAMENTAS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "vendas_por_periodo",
            "description": "Receita e numero de pedidos ao longo do tempo (por dia, semana ou mes).",
            "parameters": {
                "type": "object",
                "properties": {
                    **_PARAMETROS_PERIODO,
                    "agrupar_por": {
                        "type": "string",
                        "enum": ["dia", "semana", "mes"],
                        "description": "Granularidade do agrupamento. Padrao: dia.",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "top_itens",
            "description": (
                "Itens mais vendidos no periodo, trazendo quantidade e receita. "
                "Perguntas sobre volume ('mais vendido', 'mais pedido') usam "
                "ordenar_por='quantidade'. Perguntas sobre faturamento ('que mais "
                "faturou', 'gerou mais receita') usam ordenar_por='receita'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    **_PARAMETROS_PERIODO,
                    "limite": {"type": "integer", "description": "Quantos itens retornar. Padrao: 5."},
                    "ordenar_por": {
                        "type": "string",
                        "enum": ["quantidade", "receita"],
                        "description": "Criterio do ranking. Padrao: quantidade.",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "piores_itens",
            "description": "Itens menos vendidos no periodo: candidatos a sair do menu.",
            "parameters": {
                "type": "object",
                "properties": {
                    **_PARAMETROS_PERIODO,
                    "limite": {"type": "integer", "description": "Quantos itens retornar. Padrao: 5."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ticket_medio",
            "description": "Total de pedidos, receita e ticket medio (valor medio por pedido) no periodo.",
            "parameters": {
                "type": "object",
                "properties": {**_PARAMETROS_PERIODO},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "vendas_por_horario",
            "description": "Distribuicao de pedidos e receita por hora do dia, com a faixa (almoco, tarde, jantar, madrugada).",
            "parameters": {
                "type": "object",
                "properties": {**_PARAMETROS_PERIODO},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "consultar_sql",
            "description": (
                "Ultimo recurso: executa uma consulta SQL SELECT livre no banco. "
                "Use SOMENTE se nenhuma outra ferramenta responder. "
                "Tabelas: itens(id, nome, categoria, preco), "
                "pedidos(id, data_hora, canal, total), "
                "itens_pedido(pedido_id, item_id, quantidade, preco_unitario)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "consulta": {
                        "type": "string",
                        "description": "Uma unica instrucao SELECT ou WITH, sem ';' no meio.",
                    }
                },
                "required": ["consulta"],
            },
        },
    },
]

_FUNCOES = {
    "vendas_por_periodo": vendas_por_periodo,
    "top_itens": top_itens,
    "piores_itens": piores_itens,
    "ticket_medio": ticket_medio,
    "vendas_por_horario": vendas_por_horario,
    "consultar_sql": consultar_sql,
}


def executar_ferramenta(nome: str, argumentos: dict | None = None):
    """Executa a ferramenta pedida pelo LLM e devolve o resultado bruto.

    Retorna DataFrame ou dict. Erros de validacao sobem como excecao para que o
    agente possa devolver a mensagem ao modelo e ele tentar de novo.
    """
    argumentos = argumentos or {}
    if nome not in _FUNCOES:
        raise ValueError(f"Ferramenta desconhecida: {nome}")

    # Remove chaves nulas que o modelo costuma mandar como "None" em string.
    limpos = {
        chave: valor
        for chave, valor in argumentos.items()
        if valor is not None and valor != "" and valor != "None"
    }
    return _FUNCOES[nome](**limpos)


def mes_atual_nome() -> str:
    """Nome do mes atual em portugues (usado nos textos de apoio)."""
    meses = list(calendar.month_name)
    return meses[date.today().month]
