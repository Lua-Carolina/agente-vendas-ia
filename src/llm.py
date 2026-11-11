"""Cliente LLM configuravel por .env (qualquer API compativel com OpenAI).

Basta trocar LLM_BASE_URL / LLM_MODEL para usar Gemini, Groq, OpenAI ou Ollama
local, sem alterar nenhuma linha do resto do projeto.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

RAIZ = Path(__file__).resolve().parent.parent
load_dotenv(RAIZ / ".env")


class ErroConfiguracao(RuntimeError):
    """Levantado quando o .env nao tem o minimo para falar com o provedor."""


@dataclass(frozen=True)
class ConfigLLM:
    """Configuracao resolvida do provedor de LLM."""

    modelo: str
    api_key: str
    base_url: str | None
    temperatura: float

    @property
    def provedor(self) -> str:
        """Nome amigavel deduzido da URL (usado na interface)."""
        url = (self.base_url or "").lower()
        if "generativelanguage" in url or "google" in url:
            return "Gemini"
        if "groq" in url:
            return "Groq"
        if "localhost" in url or "127.0.0.1" in url:
            return "Ollama (local)"
        if not url or "openai" in url:
            return "OpenAI"
        return "outro provedor compativel"


def _ler(nome: str) -> str:
    """Le a configuracao do ambiente ou, se ausente, dos segredos do Streamlit.

    Localmente a configuracao vem do .env. No Streamlit Community Cloud as
    variaveis ficam em st.secrets, entao as duas fontes sao aceitas.
    """
    bruto = os.getenv(nome)
    if bruto and bruto.strip():
        return bruto.strip()
    try:
        import streamlit as st  # importado so quando necessario

        segredo = st.secrets.get(nome)
    except Exception:  # sem Streamlit, sem secrets.toml: usa o padrao
        return ""
    return str(segredo).strip() if segredo else ""


def _float_env(nome: str, padrao: float) -> float:
    bruto = _ler(nome)
    if not bruto:
        return padrao
    try:
        return float(bruto)
    except ValueError:
        return padrao


def obter_config() -> ConfigLLM:
    """Le o .env e devolve a configuracao do modelo.

    Variaveis:
        LLM_MODEL      (obrigatoria) nome do modelo, ex.: gemini-2.0-flash
        LLM_API_KEY    (obrigatoria, exceto Ollama local)
        LLM_BASE_URL   (opcional) URL compativel com OpenAI; vazio usa a oficial
        LLM_TEMPERATURA(opcional, padrao 0.0)
    """
    modelo = _ler("LLM_MODEL")
    api_key = _ler("LLM_API_KEY")
    base_url = _ler("LLM_BASE_URL") or None

    if not modelo:
        raise ErroConfiguracao(
            "LLM_MODEL nao definido no .env. Exemplo: LLM_MODEL=gemini-2.0-flash"
        )

    local = bool(base_url) and ("localhost" in base_url or "127.0.0.1" in base_url)
    if not api_key:
        if local:
            api_key = "ollama"  # o Ollama aceita qualquer chave
        else:
            raise ErroConfiguracao(
                "LLM_API_KEY nao definida no .env. Coloque a chave do provedor "
                "(ou aponte LLM_BASE_URL para o Ollama local, que dispensa chave)."
            )

    return ConfigLLM(
        modelo=modelo,
        api_key=api_key,
        base_url=base_url,
        temperatura=_float_env("LLM_TEMPERATURA", 0.0),
    )


def criar_cliente(config: ConfigLLM | None = None) -> OpenAI:
    """Cria o cliente OpenAI apontando para o provedor configurado."""
    config = config or obter_config()
    return OpenAI(api_key=config.api_key, base_url=config.base_url)


def descricao_config() -> str:
    """Texto curto para exibir na interface, sem nunca mostrar a chave."""
    try:
        config = obter_config()
    except ErroConfiguracao:
        return "nao configurado"
    return f"{config.modelo} via {config.provedor}"
