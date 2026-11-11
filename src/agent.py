"""Loop de tool calling: pergunta em portugues -> ferramenta segura -> resposta.

O LLM nunca ve os dados crus nem calcula nada: ele escolhe qual ferramenta
chamar, o Python executa no DuckDB e so o resultado agregado volta para o
modelo, que entao redige a explicacao.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from functools import lru_cache

import pandas as pd
import plotly.express as px
from plotly.graph_objects import Figure

from src.llm import ErroConfiguracao, criar_cliente, obter_config
from src.tools import FERRAMENTAS, executar_ferramenta

MAX_PASSOS = 6

PROMPT_SISTEMA = """Voce e um analista de vendas de um pequeno restaurante.

Hoje e {hoje}. Os dados de vendas disponiveis cobrem de {inicio} a {fim}.
O primeiro e o ultimo mes dessa serie podem estar incompletos, porque os dados
comecam e terminam no meio de um mes: nao trate isso como queda ou crescimento.

Como trabalhar:
- Sempre consulte uma ferramenta antes de responder. Voce NAO sabe os numeros de cabeca.
- Escolha a ferramenta mais especifica: itens mais/menos vendidos, ticket medio,
  vendas ao longo do tempo ou por horario. Use consultar_sql apenas se nenhuma
  das outras responder a pergunta.
- Nunca invente numeros. Se a ferramenta retornar vazio, diga que nao ha dados
  para o periodo em vez de estimar.

Como responder:
- Em portugues do Brasil, tom direto e simples, para um dono de restaurante
  que nao entende de tecnologia.
- Comece pela resposta objetiva (o numero principal) em uma ou duas frases.
- Depois, se fizer sentido, de no maximo 3 recomendacoes praticas e curtas.
- Formate dinheiro como R$ 1.234,56 e quantidades como 1.234.
- Nao descreva SQL, nomes de ferramentas ou detalhes tecnicos.
- Se a pergunta nao for sobre as vendas do restaurante, diga educadamente que
  so consegue ajudar com os dados de vendas.
"""


@dataclass
class Consulta:
    """Uma chamada de ferramenta feita durante a resposta."""

    ferramenta: str
    argumentos: dict
    dados: object


@dataclass
class RespostaAgente:
    """Resposta final do agente, com o grafico e a trilha de consultas."""

    texto: str
    figura: Figure | None = None
    consultas: list[Consulta] = field(default_factory=list)


@lru_cache(maxsize=1)
def _periodo_disponivel() -> tuple[str, str]:
    """Descobre o intervalo de datas realmente presente no banco."""
    try:
        from src.tools import conectar

        resultado = conectar().execute(
            "SELECT min(data_hora::date), max(data_hora::date) FROM pedidos"
        ).fetchone()
        if resultado and resultado[0]:
            return str(resultado[0]), str(resultado[1])
    except Exception:
        pass
    return "desconhecido", "desconhecido"


def _como_texto(dados: object, max_linhas: int = 30) -> str:
    """Converte o resultado da ferramenta em texto para o modelo."""
    if isinstance(dados, pd.DataFrame):
        if dados.empty:
            return "A consulta nao retornou linhas para esse periodo."
        return dados.head(max_linhas).to_string(index=False)
    if isinstance(dados, dict):
        return json.dumps(dados, ensure_ascii=False)
    return str(dados)


def _grafico_de(consulta: Consulta) -> Figure | None:
    """Escolhe o tipo de grafico adequado para a ferramenta usada."""
    dados = consulta.dados
    if not isinstance(dados, pd.DataFrame) or dados.empty:
        return None

    colunas = set(dados.columns)
    layout = {
        "template": "plotly_white",
        "margin": dict(l=10, r=10, t=56, b=10),
        "font": dict(family="Inter, Segoe UI, sans-serif", color="#26332C", size=13),
        "title": dict(font=dict(size=15, color="#132019"), x=0, xanchor="left"),
        "colorway": ["#1E6F5C", "#2E9E7E", "#E8A33D", "#B4552F", "#7C8A82"],
        "paper_bgcolor": "rgba(0,0,0,0)",
        "plot_bgcolor": "rgba(0,0,0,0)",
    }

    if consulta.ferramenta == "vendas_por_periodo" and {"periodo", "receita"} <= colunas:
        fig = px.line(
            dados, x="periodo", y="receita", markers=True,
            title="Receita ao longo do tempo",
            labels={"periodo": "Periodo", "receita": "Receita (R$)"},
        )
        return fig.update_layout(**layout)

    if consulta.ferramenta in ("top_itens", "piores_itens") and {"nome", "quantidade"} <= colunas:
        titulo = "Itens mais vendidos" if consulta.ferramenta == "top_itens" else "Itens menos vendidos"
        ordenado = dados.sort_values("quantidade", ascending=consulta.ferramenta == "top_itens")
        fig = px.bar(
            ordenado, x="quantidade", y="nome", orientation="h",
            title=titulo,
            labels={"quantidade": "Unidades vendidas", "nome": ""},
        )
        return fig.update_layout(**layout)

    if consulta.ferramenta == "vendas_por_horario" and {"hora", "pedidos"} <= colunas:
        fig = px.bar(
            dados, x="hora", y="pedidos",
            title="Pedidos por hora do dia",
            labels={"hora": "Hora", "pedidos": "Pedidos"},
        )
        fig.update_xaxes(dtick=1)
        return fig.update_layout(**layout)

    # consultar_sql: heuristica generica (primeira coluna textual x primeira numerica)
    if consulta.ferramenta == "consultar_sql" and dados.shape[1] >= 2:
        texto = [c for c in dados.columns if not pd.api.types.is_numeric_dtype(dados[c])]
        numero = [c for c in dados.columns if pd.api.types.is_numeric_dtype(dados[c])]
        if texto and numero and len(dados) <= 50:
            fig = px.bar(dados, x=texto[0], y=numero[0], title="Resultado da consulta")
            return fig.update_layout(**layout)

    return None


def _montar_grafico(consultas: list[Consulta]) -> Figure | None:
    """Usa a ultima consulta que render um grafico util."""
    for consulta in reversed(consultas):
        figura = _grafico_de(consulta)
        if figura is not None:
            return figura
    return None


def responder(
    pergunta: str,
    historico: list[dict] | None = None,
    cliente=None,
    max_passos: int = MAX_PASSOS,
) -> RespostaAgente:
    """Responde uma pergunta em linguagem natural sobre as vendas.

    Args:
        pergunta: texto do usuario.
        historico: mensagens anteriores [{"role": "user"|"assistant", "content": str}].
        cliente: cliente OpenAI opcional (reaproveitado pela interface).
    """
    config = obter_config()
    cliente = cliente or criar_cliente(config)

    inicio, fim = _periodo_disponivel()
    mensagens: list[dict] = [
        {
            "role": "system",
            "content": PROMPT_SISTEMA.format(
                hoje=date.today().strftime("%d/%m/%Y"), inicio=inicio, fim=fim
            ),
        }
    ]
    for mensagem in historico or []:
        if mensagem.get("role") in ("user", "assistant") and mensagem.get("content"):
            mensagens.append({"role": mensagem["role"], "content": mensagem["content"]})
    mensagens.append({"role": "user", "content": pergunta})

    consultas: list[Consulta] = []

    for _ in range(max_passos):
        resposta = cliente.chat.completions.create(
            model=config.modelo,
            messages=mensagens,
            tools=FERRAMENTAS,
            temperature=config.temperatura,
        )
        escolha = resposta.choices[0].message

        if not escolha.tool_calls:
            texto = (escolha.content or "").strip() or (
                "Nao consegui responder com os dados disponiveis. Pode reformular a pergunta?"
            )
            return RespostaAgente(texto=texto, figura=_montar_grafico(consultas), consultas=consultas)

        mensagens.append(
            {
                "role": "assistant",
                "content": escolha.content or "",
                "tool_calls": [
                    {
                        "id": chamada.id,
                        "type": "function",
                        "function": {
                            "name": chamada.function.name,
                            "arguments": chamada.function.arguments,
                        },
                    }
                    for chamada in escolha.tool_calls
                ],
            }
        )

        for chamada in escolha.tool_calls:
            nome = chamada.function.name
            try:
                argumentos = json.loads(chamada.function.arguments or "{}")
            except json.JSONDecodeError:
                mensagens.append(
                    {
                        "role": "tool",
                        "tool_call_id": chamada.id,
                        "content": "ERRO: argumentos invalidos em JSON. Tente novamente.",
                    }
                )
                continue

            try:
                dados = executar_ferramenta(nome, argumentos)
                consultas.append(Consulta(nome, argumentos, dados))
                conteudo = _como_texto(dados)
            except Exception as erro:  # erro de validacao ou consulta: o modelo ve e corrige
                conteudo = f"ERRO na ferramenta {nome}: {erro}"

            mensagens.append(
                {"role": "tool", "tool_call_id": chamada.id, "content": conteudo}
            )

    return RespostaAgente(
        texto=(
            "Nao consegui concluir a analise em tempo. Tente uma pergunta mais "
            "especifica, como: 'quais foram os 5 pratos mais vendidos no ultimo mes?'"
        ),
        figura=_montar_grafico(consultas),
        consultas=consultas,
    )


def resumo_semanal(cliente=None) -> RespostaAgente:
    """Gera o resumo da semana com recomendacoes acionaveis."""
    pergunta = (
        "Monte o resumo da ultima semana de vendas para o dono do restaurante: "
        "receita total, numero de pedidos e ticket medio; qual foi o prato que mais "
        "vendeu e qual o que menos vendeu; em que horario o movimento se concentra. "
        "Termine com ate 3 recomendacoes praticas para a proxima semana."
    )
    return responder(pergunta, cliente=cliente)
