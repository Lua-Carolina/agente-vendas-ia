"""Interface Streamlit do agente de analise de vendas.

Rode com:  streamlit run app.py
"""

from __future__ import annotations

import html

import streamlit as st

from src.agent import resumo_semanal, responder
from src.llm import ErroConfiguracao, criar_cliente, descricao_config
from src.tools import BancoIndisponivelError, ticket_medio, top_itens

MARCA = "Mesa Analytics"
SUBTITULO = "Inteligencia de vendas para restaurantes"
MARCADOR_RESUMO = "__RESUMO_SEMANAL__"

SUGESTOES = [
    (
        "🏆",
        "Mais vendidos",
        "Quais foram os 5 pratos mais vendidos no mes passado?",
    ),
    (
        "📉",
        "Menos vendidos",
        "Qual prato vendeu menos no mes passado?",
    ),
    (
        "🧾",
        "Ticket medio",
        "Qual foi o ticket medio da ultima semana?",
    ),
    (
        "🕒",
        "Horario de pico",
        "Em que horario o restaurante vende mais?",
    ),
]

CSS = """
<style>
  .stApp {
    background:
      radial-gradient(1200px 500px at 50% -200px, #EAF4EF 0%, rgba(251,250,247,0) 70%),
      #FBFAF7;
  }
  .block-container { padding-top: 1.6rem; padding-bottom: 6rem; max-width: 1080px; }
  header[data-testid="stHeader"] { background: transparent; }
  #MainMenu, footer { visibility: hidden; }

  h1, h2, h3, h4 { color: #132019; letter-spacing: -0.02em; }

  /* --- marca --- */
  .marca { display: flex; align-items: center; gap: .75rem; margin-bottom: 1.1rem; }
  .marca-selo {
    width: 42px; height: 42px; border-radius: 12px; flex: 0 0 auto;
    background: linear-gradient(135deg, #1E6F5C 0%, #2E9E7E 100%);
    display: grid; place-items: center; font-size: 20px;
    box-shadow: 0 6px 16px rgba(30,111,92,.22);
  }
  .marca-nome { font-size: 1.22rem; font-weight: 750; color: #132019; line-height: 1.1; }
  .marca-sub { font-size: .78rem; color: #6B7A72; }

  /* --- hero do estado vazio --- */
  .hero {
    border: 1px solid #E7E1D5; border-radius: 18px; padding: 1.4rem 1.5rem;
    background: linear-gradient(135deg, #FFFFFF 0%, #F7FBF8 100%);
    margin-bottom: 1.1rem;
  }
  .hero-titulo { font-size: 1.5rem; font-weight: 750; color: #132019; letter-spacing: -0.02em; }
  .hero-texto { color: #56655D; font-size: .93rem; margin-top: .35rem; max-width: 62ch; }
  .hero-nota { color: #7C8A82; font-size: .8rem; margin-top: .8rem; }

  /* --- KPIs --- */
  .kpi {
    background: #FFFFFF; border: 1px solid #E9E3D6; border-radius: 14px;
    padding: .8rem .95rem; height: 100%;
  }
  .kpi-rotulo {
    font-size: .68rem; text-transform: uppercase; letter-spacing: .09em;
    color: #6B7A72; font-weight: 700;
  }
  .kpi-valor {
    display: block; font-size: 1.45rem; font-weight: 750; color: #132019;
    line-height: 1.3; margin-top: .15rem; white-space: nowrap;
    overflow: hidden; text-overflow: ellipsis;
  }
  .kpi-detalhe { font-size: .74rem; color: #7C8A82; }

  /* --- conversa --- */
  [data-testid="stChatMessage"] {
    background: #FFFFFF; border: 1px solid #EDE7DB; border-radius: 16px;
    padding: .5rem .7rem; margin-bottom: .55rem;
  }
  [data-testid="stChatMessage"] p { color: #26332C; }

  /* --- botoes --- */
  .stButton > button {
    width: 100%; border-radius: 12px; border: 1px solid #E3DCCD;
    background: #FFFFFF; color: #17211B; font-weight: 600;
    padding: .55rem .8rem; text-align: left; justify-content: flex-start;
    transition: border-color .15s ease, color .15s ease, background .15s ease;
  }
  .stButton > button:hover {
    border-color: #1E6F5C; color: #1E6F5C; background: #F5FBF8;
  }
  .stButton > button[kind="primary"] {
    background: #1E6F5C; border-color: #1E6F5C; color: #FFFFFF; justify-content: center;
  }
  .stButton > button[kind="primary"]:hover { background: #185948; color: #FFFFFF; }

  /* --- barra lateral --- */
  [data-testid="stSidebar"] { background: #FFFFFF; border-right: 1px solid #EDE7DB; }
  [data-testid="stSidebar"] .stButton > button { font-size: .82rem; padding: .45rem .6rem; }
  .lateral-secao {
    font-size: .68rem; text-transform: uppercase; letter-spacing: .09em;
    color: #6B7A72; font-weight: 700; margin: .2rem 0 .35rem;
  }
  .pino {
    display: inline-flex; align-items: center; gap: .4rem;
    background: #F1F7F3; border: 1px solid #D9E9DF; color: #1E6F5C;
    border-radius: 999px; padding: .22rem .6rem; font-size: .74rem; font-weight: 600;
  }
  .pino.off { background: #FDF3E7; border-color: #F0D9B8; color: #9A5B12; }
</style>
"""


def _brl(valor: float) -> str:
    """Formata um numero como moeda brasileira."""
    texto = f"{valor:,.2f}"
    return "R$ " + texto.replace(",", "X").replace(".", ",").replace("X", ".")


def _inteiro(valor: float) -> str:
    """Formata um numero inteiro com separador de milhar."""
    return f"{int(valor):,}".replace(",", ".")


def _estado() -> None:
    """Inicializa o estado da sessao (sobrevive aos reruns do Streamlit)."""
    st.session_state.setdefault("mensagens", [])
    st.session_state.setdefault("pedido", None)


@st.cache_resource(show_spinner=False)
def _cliente_llm():
    """Cria o cliente do LLM uma unica vez por processo."""
    return criar_cliente()


@st.cache_resource(show_spinner="Preparando os dados de demonstracao...")
def _garantir_banco() -> bool:
    """Gera o banco sintetico na primeira execucao.

    O arquivo .duckdb fica fora do git, entao em um deploy limpo (Streamlit
    Community Cloud, por exemplo) ele precisa ser criado na hora.
    """
    try:
        from data.gerar_dados import CAMINHO_BANCO, gerar

        if not CAMINHO_BANCO.exists():
            gerar()
        return True
    except Exception:
        return False


@st.cache_data(ttl=300, show_spinner=False)
def _kpis() -> dict | None:
    """Resume os ultimos 30 dias para a faixa de indicadores."""
    try:
        resumo = ticket_medio("ultimos_30_dias")
        lideres = top_itens("ultimos_30_dias", limite=1)
        lider = None
        if not lideres.empty:
            linha = lideres.iloc[0]
            lider = {"nome": str(linha["nome"]), "quantidade": float(linha["quantidade"])}
        return {"resumo": resumo, "lider": lider}
    except Exception:  # banco ausente ou vazio: a faixa simplesmente some
        return None


def _historico() -> list[dict]:
    """Historico enxuto (apenas texto) para dar contexto ao modelo."""
    return [
        {"role": msg["role"], "content": msg["conteudo"]}
        for msg in st.session_state.mensagens
    ]


def _mostrar_consultas(consultas) -> None:
    """Mostra, em detalhe recolhido, quais ferramentas o agente usou."""
    if not consultas:
        return
    with st.expander("Ver dados consultados"):
        for consulta in consultas:
            st.caption(f"Ferramenta: {consulta.ferramenta} {consulta.argumentos}")
            if hasattr(consulta.dados, "to_string"):
                st.dataframe(consulta.dados, hide_index=True)
            else:
                st.json(consulta.dados)


def _cabecalho() -> None:
    """Desenha a marca no topo da pagina."""
    st.markdown(
        f"""
        <div class="marca">
          <div class="marca-selo">🍽️</div>
          <div>
            <div class="marca-nome">{MARCA}</div>
            <div class="marca-sub">{SUBTITULO}</div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _faixa_kpis() -> None:
    """Mostra os quatro numeros que o dono olha primeiro."""
    dados = _kpis()
    if not dados:
        return

    resumo = dados["resumo"]
    lider = dados["lider"]
    cartoes = [
        ("Receita (30 dias)", _brl(resumo["receita_total"]), f"{_inteiro(resumo['pedidos'])} pedidos"),
        ("Ticket medio", _brl(resumo["ticket_medio"]), "por pedido"),
        (
            "Item lider",
            html.escape(lider["nome"]) if lider else "sem dados",
            f"{_inteiro(lider['quantidade'])} unidades" if lider else "",
        ),
        ("Modelo", descricao_config(), "respostas conferidas no banco"),
    ]

    colunas = st.columns(4, gap="small")
    for coluna, (rotulo, valor, detalhe) in zip(colunas, cartoes):
        coluna.markdown(
            f"""
            <div class="kpi">
              <div class="kpi-rotulo">{rotulo}</div>
              <span class="kpi-valor">{valor}</span>
              <div class="kpi-detalhe">{detalhe}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    st.write("")


def _estado_inicial() -> None:
    """Tela de abertura: explicacao curta e atalhos de pergunta."""
    st.markdown(
        """
        <div class="hero">
          <div class="hero-titulo">Pergunte as suas vendas</div>
          <div class="hero-texto">
            Escreva do seu jeito, sem planilha e sem termo tecnico. O assistente
            consulta os pedidos registrados e responde com numeros reais do seu
            restaurante.
          </div>
          <div class="hero-nota">Dica: comece por uma das perguntas abaixo.</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    for inicio in range(0, len(SUGESTOES), 2):
        colunas = st.columns(2, gap="small")
        for coluna, (icone, titulo, pergunta) in zip(colunas, SUGESTOES[inicio : inicio + 2]):
            with coluna:
                if st.button(f"{icone}  {titulo}", key=f"atalho_{titulo}", width="stretch"):
                    st.session_state.pedido = pergunta


def _barra_lateral() -> None:
    """Menu de acoes, exemplos e status da configuracao."""
    with st.sidebar:
        _cabecalho()

        if st.button("📌  Resumo da semana", type="primary", width="stretch"):
            st.session_state.pedido = MARCADOR_RESUMO

        st.write("")

        st.markdown('<div class="lateral-secao">Perguntas frequentes</div>', unsafe_allow_html=True)
        for exemplo in SUGESTOES:
            if st.button(exemplo[2], key=f"exemplo_{exemplo[1]}", width="stretch"):
                st.session_state.pedido = exemplo[2]

        st.write("")
        if st.button("🧹  Limpar conversa", width="stretch"):
            st.session_state.mensagens = []

        st.divider()

        modelo = descricao_config()
        if modelo == "nao configurado":
            st.markdown(
                '<div class="pino off">⚙️ Modelo nao configurado</div>',
                unsafe_allow_html=True,
            )
            st.caption("Crie o arquivo `.env` com `LLM_MODEL` e `LLM_API_KEY`.")
        else:
            st.markdown(f'<div class="pino">● {modelo}</div>', unsafe_allow_html=True)
            st.caption(
                "A chave fica no servidor. O assistente nunca inventa numeros: "
                "toda resposta vem de uma consulta ao banco de vendas."
            )


def _processar(pergunta: str) -> None:
    """Envia a pergunta ao agente e desenha a resposta na tela."""
    historico = _historico()

    st.session_state.mensagens.append({"role": "user", "conteudo": pergunta})
    with st.chat_message("user", avatar="🧑‍🍳"):
        st.markdown(pergunta)

    with st.chat_message("assistant", avatar="📊"):
        with st.spinner("Consultando as vendas..."):
            try:
                if pergunta == MARCADOR_RESUMO:
                    resposta = resumo_semanal(cliente=_cliente_llm())
                else:
                    resposta = responder(
                        pergunta, historico=historico, cliente=_cliente_llm()
                    )
            except ErroConfiguracao as erro:
                st.error(f"Configuracao pendente: {erro}")
                st.session_state.mensagens.pop()
                return
            except BancoIndisponivelError:
                st.error(
                    "Banco de dados nao encontrado. Rode `python data/gerar_dados.py` "
                    "e recarregue a pagina."
                )
                st.session_state.mensagens.pop()
                return
            except Exception as erro:  # rede, chave invalida, modelo inexistente...
                st.error(
                    "Nao consegui falar com o modelo agora. Confira LLM_MODEL e "
                    f"LLM_API_KEY no .env. Detalhe tecnico: {erro}"
                )
                st.session_state.mensagens.pop()
                return

        st.markdown(resposta.texto)
        if resposta.figura is not None:
            st.plotly_chart(
                resposta.figura, theme=None, config={"displayModeBar": False}
            )
        _mostrar_consultas(resposta.consultas)

    st.session_state.mensagens.append(
        {
            "role": "assistant",
            "conteudo": resposta.texto,
            "figura": resposta.figura,
            "consultas": resposta.consultas,
        }
    )


def main() -> None:
    """Monta a pagina."""
    st.set_page_config(
        page_title=f"{MARCA} - Vendas do restaurante",
        page_icon="🍽️",
        layout="wide",
    )
    _estado()
    st.markdown(CSS, unsafe_allow_html=True)
    _garantir_banco()

    _barra_lateral()
    _cabecalho()
    _faixa_kpis()

    if not st.session_state.mensagens:
        _estado_inicial()

    for mensagem in st.session_state.mensagens:
        avatar = "🧑‍🍳" if mensagem["role"] == "user" else "📊"
        with st.chat_message(mensagem["role"], avatar=avatar):
            st.markdown(mensagem["conteudo"])
            if mensagem.get("figura") is not None:
                st.plotly_chart(
                    mensagem["figura"], theme=None, config={"displayModeBar": False}
                )
            if mensagem["role"] == "assistant":
                _mostrar_consultas(mensagem.get("consultas"))

    pergunta = st.chat_input("Pergunte sobre as vendas...")
    if st.session_state.pedido:
        pergunta = st.session_state.pedido
        st.session_state.pedido = None

    if pergunta:
        _processar(pergunta)


if __name__ == "__main__":
    main()
