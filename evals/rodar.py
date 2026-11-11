"""Executa as perguntas de eval contra o agente e mede a taxa de acerto.

Uso:
    python evals/rodar.py
    python evals/rodar.py --detalhado

Cada pergunta e verificada por fatos (nome do item, valor numerico dentro de uma
tolerancia), nunca por texto exato: a redacao muda a cada execucao, o dado nao.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import unicodedata
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from src.agent import responder  # noqa: E402
from src.llm import ErroConfiguracao, criar_cliente  # noqa: E402

ARQUIVO = RAIZ / "evals" / "perguntas.json"


def normalizar(texto: str) -> str:
    """Minusculas e sem acento, para comparar 'almoço' com 'almoco'."""
    sem_acento = unicodedata.normalize("NFKD", texto)
    sem_acento = "".join(c for c in sem_acento if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", sem_acento).lower()


def extrair_numeros(texto: str) -> list[float]:
    """Extrai numeros do texto aceitando formato brasileiro (1.234,56)."""
    numeros: list[float] = []
    for bruto in re.findall(r"\d[\d.,]*", texto):
        token = bruto.rstrip(".,")
        if not token:
            continue
        if "," in token:
            limpo = token.replace(".", "").replace(",", ".")
        elif token.count(".") > 1:
            limpo = token.replace(".", "")
        elif "." in token and len(token.split(".")[-1]) == 3:
            limpo = token.replace(".", "")
        else:
            limpo = token
        try:
            numeros.append(float(limpo))
        except ValueError:
            continue
    return numeros


def verificar(resposta: str, esperado: dict) -> list[str]:
    """Devolve a lista de problemas encontrados (vazia = passou)."""
    texto = normalizar(resposta)
    falhas: list[str] = []

    for termo in esperado.get("contem", []):
        if normalizar(termo) not in texto:
            falhas.append(f"nao citou '{termo}'")

    alternativas = esperado.get("contem_qualquer", [])
    if alternativas and not any(normalizar(t) in texto for t in alternativas):
        falhas.append(f"nao citou nenhum de {alternativas}")

    for termo in esperado.get("nao_contem", []):
        if normalizar(termo) in texto:
            falhas.append(f"citou '{termo}', que nao deveria aparecer")

    if "numero" in esperado:
        alvo = float(esperado["numero"])
        tolerancia = float(esperado.get("tolerancia", 0.0))
        numeros = extrair_numeros(resposta)
        if not any(abs(n - alvo) <= tolerancia for n in numeros):
            falhas.append(f"esperava {alvo} (+-{tolerancia}); achou {numeros[:8]}")

    return falhas


def main() -> int:
    parser = argparse.ArgumentParser(description="Roda a suite de avaliacao do agente.")
    parser.add_argument("--arquivo", default=str(ARQUIVO), help="JSON com as perguntas.")
    parser.add_argument("--detalhado", action="store_true", help="Mostra a resposta completa de cada pergunta.")
    args = parser.parse_args()

    dados = json.loads(Path(args.arquivo).read_text(encoding="utf-8"))
    perguntas = dados["perguntas"]

    try:
        cliente = criar_cliente()
    except ErroConfiguracao as erro:
        print(f"Configuracao ausente: {erro}")
        print("Preencha o .env antes de rodar as evals.")
        return 2

    print(f"Suite: {args.arquivo} ({len(perguntas)} perguntas)")
    print(f"Dados: {dados.get('periodo_dados', 'nao informado')}\n")

    acertos = 0
    duracao_total = 0.0

    for item in perguntas:
        inicio = time.perf_counter()
        try:
            resposta = responder(item["pergunta"], cliente=cliente)
            texto = resposta.texto
            falhas = verificar(texto, item)
        except Exception as erro:  # falha de API nao deve derrubar a suite inteira
            texto = ""
            falhas = [f"erro ao executar: {erro}"]
        duracao = time.perf_counter() - inicio
        duracao_total += duracao

        passou = not falhas
        acertos += int(passou)
        marca = "PASS" if passou else "FAIL"
        print(f"[{marca}] {item['id']} ({duracao:.1f}s)")
        print(f"       pergunta: {item['pergunta']}")
        if args.detalhado:
            print(f"       resposta: {texto}")
        if not passou:
            for falha in falhas:
                print(f"       -> {falha}")
        print()

    total = len(perguntas)
    print(f"Acerto: {acertos}/{total} ({acertos / total:.0%}) em {duracao_total:.1f}s")
    return 0 if acertos == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
