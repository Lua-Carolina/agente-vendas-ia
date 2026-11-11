"""Gera 6 meses de vendas sinteticas de um restaurante no DuckDB.

Uso:
    python data/gerar_dados.py
    python data/gerar_dados.py --dias 180 --semente 42

O banco gerado (data/vendas.duckdb) e um artefato descartavel: ele nao vai
para o git (veja .gitignore) e pode ser recriado a qualquer momento.
"""

from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

RAIZ = Path(__file__).resolve().parent.parent
CAMINHO_BANCO = RAIZ / "data" / "vendas.duckdb"

# ---------------------------------------------------------------------------
# Cardapio: (nome, categoria, preco, popularidade, tendencia)
# popularidade = peso relativo de venda; tendencia = multiplicador no inicio
# e no fim do periodo (1.0 -> 0.2 significa item morrendo, bom para a IA
# recomendar tirar do menu; 1.0 -> 1.6 significa item crescendo).
# ---------------------------------------------------------------------------
CARDAPIO = [
    ("Feijoada completa", "Prato principal", 54.90, 9.0, 1.00, 1.05),
    ("Picanha na chapa", "Prato principal", 89.90, 6.0, 1.00, 1.10),
    ("Frango grelhado com arroz", "Prato principal", 38.90, 8.0, 1.00, 1.00),
    ("Moqueca de peixe", "Prato principal", 72.50, 5.0, 1.00, 1.15),
    ("Escondidinho de carne seca", "Prato principal", 49.90, 7.0, 1.00, 1.25),
    ("Risoto de funghi", "Prato principal", 64.00, 3.5, 1.00, 1.30),
    ("Sopa de legumes", "Prato principal", 24.90, 4.0, 1.00, 0.18),
    ("Salada Caesar", "Entrada", 32.00, 5.0, 1.00, 1.10),
    ("Bolinho de bacalhau (6 un)", "Entrada", 39.90, 4.5, 1.00, 1.00),
    ("Pastel de queijo (4 un)", "Entrada", 22.00, 6.0, 1.00, 0.85),
    ("Pudim de leite", "Sobremesa", 18.90, 5.5, 1.00, 1.05),
    ("Petit gateau", "Sobremesa", 27.90, 4.0, 1.00, 1.20),
    ("Brigadeiro gourmet", "Sobremesa", 12.50, 4.5, 1.00, 1.00),
    ("Suco de laranja 500ml", "Bebida", 14.00, 8.5, 1.00, 1.00),
    ("Refrigerante lata", "Bebida", 8.00, 10.0, 1.00, 0.95),
    ("Cerveja long neck", "Bebida", 12.00, 9.5, 1.00, 1.10),
    ("Agua mineral 500ml", "Bebida", 6.00, 7.0, 1.00, 1.00),
    ("Cafe espresso", "Bebida", 9.00, 6.0, 1.00, 1.00),
]

CANAIS = ["salao", "delivery", "balcao"]
PROB_CANAL = [0.55, 0.30, 0.15]

# Faixas de horario com peso: almoco e jantar sao os picos.
FAIXAS_HORARIO = [
    ((11, 15), 0.38),   # almoco
    ((15, 18), 0.10),   # tarde
    ((18, 23), 0.46),   # jantar
    ((0, 2), 0.06),     # madrugada (bar)
]

# Fator por dia da semana (segunda=0 ... domingo=6)
FATOR_DIA = [0.82, 0.82, 0.88, 0.95, 1.18, 1.38, 1.15]


def _gerar_itens() -> pd.DataFrame:
    """Monta o DataFrame do cardapio (tabela itens)."""
    linhas = [
        {"id": i + 1, "nome": nome, "categoria": categoria, "preco": preco}
        for i, (nome, categoria, preco, _, _, _) in enumerate(CARDAPIO)
    ]
    return pd.DataFrame(linhas)


def _gerar_pedidos(
    inicio: date, dias: int, semente: int
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Gera as tabelas pedidos e itens_pedido de forma deterministica."""
    rng = np.random.default_rng(semente)

    itens = _gerar_itens()
    precos = itens["preco"].to_numpy(dtype=float)
    popularidade = np.array([linha[3] for linha in CARDAPIO], dtype=float)
    tendencia = np.array(
        [[linha[4], linha[5]] for linha in CARDAPIO], dtype=float
    )

    pedidos: list[dict] = []
    linhas_item: list[dict] = []
    id_pedido = 0

    for d in range(dias):
        dia = inicio + timedelta(days=d)
        progresso = d / max(dias - 1, 1)  # 0.0 no primeiro dia, 1.0 no ultimo

        # Tendencia linear do item ao longo do periodo.
        pesos = popularidade * (
            tendencia[:, 0] + (tendencia[:, 1] - tendencia[:, 0]) * progresso
        )
        probabilidades = pesos / pesos.sum()

        # Sazonalidade leve + dia da semana.
        base = 78 * FATOR_DIA[dia.weekday()] * (1 + 0.06 * np.sin(2 * np.pi * progresso))
        n_pedidos = max(1, int(rng.normal(base, base * 0.12)))

        for _ in range(n_pedidos):
            id_pedido += 1

            # Faixa de horario sorteada por peso
            faixas, pesos_faixa = zip(*FAIXAS_HORARIO)
            faixa = faixas[rng.choice(len(faixas), p=np.array(pesos_faixa) / sum(pesos_faixa))]
            hora = int(rng.integers(faixa[0], faixa[1]))
            minuto = int(rng.integers(0, 60))
            data_hora = datetime(dia.year, dia.month, dia.day, hora, minuto)

            canal = CANAIS[rng.choice(len(CANAIS), p=PROB_CANAL)]

            # 1 a 4 itens distintos por pedido
            qtd_itens = int(rng.choice([1, 2, 2, 3, 3, 4], p=[0.14, 0.24, 0.2, 0.2, 0.14, 0.08]))
            escolhidos = rng.choice(len(CARDAPIO), size=qtd_itens, replace=False, p=probabilidades)

            total = 0.0
            for idx in escolhidos:
                quantidade = int(rng.choice([1, 1, 1, 2, 2, 3], p=[0.45, 0.2, 0.1, 0.15, 0.06, 0.04]))
                preco_unitario = float(precos[idx])
                total += quantidade * preco_unitario
                linhas_item.append(
                    {
                        "pedido_id": id_pedido,
                        "item_id": int(idx) + 1,
                        "quantidade": quantidade,
                        "preco_unitario": round(preco_unitario, 2),
                    }
                )

            pedidos.append(
                {
                    "id": id_pedido,
                    "data_hora": data_hora,
                    "canal": canal,
                    "total": round(total, 2),
                }
            )

    return pd.DataFrame(pedidos), pd.DataFrame(linhas_item)


def _gravar(caminho: Path, itens: pd.DataFrame, pedidos: pd.DataFrame, linhas: pd.DataFrame) -> None:
    """Cria (ou recria) o banco DuckDB com as tres tabelas."""
    caminho.parent.mkdir(parents=True, exist_ok=True)
    if caminho.exists():
        caminho.unlink()

    con = duckdb.connect(str(caminho))
    try:
        con.execute(
            """
            CREATE TABLE itens (
                id INTEGER PRIMARY KEY,
                nome VARCHAR NOT NULL,
                categoria VARCHAR NOT NULL,
                preco DECIMAL(10, 2) NOT NULL
            )
            """
        )
        con.execute(
            """
            CREATE TABLE pedidos (
                id BIGINT PRIMARY KEY,
                data_hora TIMESTAMP NOT NULL,
                canal VARCHAR NOT NULL,
                total DECIMAL(10, 2) NOT NULL
            )
            """
        )
        con.execute(
            """
            CREATE TABLE itens_pedido (
                pedido_id BIGINT NOT NULL,
                item_id INTEGER NOT NULL,
                quantidade INTEGER NOT NULL,
                preco_unitario DECIMAL(10, 2) NOT NULL
            )
            """
        )

        con.register("df_itens", itens)
        con.register("df_pedidos", pedidos)
        con.register("df_linhas", linhas)

        con.execute("INSERT INTO itens SELECT * FROM df_itens")
        con.execute("INSERT INTO pedidos SELECT * FROM df_pedidos")
        con.execute("INSERT INTO itens_pedido SELECT * FROM df_linhas")

        con.unregister("df_itens")
        con.unregister("df_pedidos")
        con.unregister("df_linhas")
    finally:
        con.close()


def gerar(
    caminho: Path = CAMINHO_BANCO,
    dias: int = 180,
    fim: date | None = None,
    semente: int = 42,
) -> Path:
    """Gera os dados sinteticos e devolve o caminho do banco criado."""
    fim = fim or date.today()
    inicio = fim - timedelta(days=dias - 1)

    itens = _gerar_itens()
    pedidos, linhas = _gerar_pedidos(inicio, dias, semente)
    _gravar(caminho, itens, pedidos, linhas)
    return caminho


def main() -> None:
    parser = argparse.ArgumentParser(description="Gera dados sinteticos de vendas no DuckDB.")
    parser.add_argument("--dias", type=int, default=180, help="Quantidade de dias (padrao: 180).")
    parser.add_argument("--fim", type=str, default=None, help="Ultimo dia no formato AAAA-MM-DD (padrao: hoje).")
    parser.add_argument("--semente", type=int, default=42, help="Semente aleatoria (padrao: 42).")
    parser.add_argument("--saida", type=str, default=str(CAMINHO_BANCO), help="Caminho do arquivo .duckdb.")
    args = parser.parse_args()

    fim = date.fromisoformat(args.fim) if args.fim else date.today()
    caminho = Path(args.saida)

    gerar(caminho=caminho, dias=args.dias, fim=fim, semente=args.semente)

    con = duckdb.connect(str(caminho), read_only=True)
    try:
        n_itens = con.execute("SELECT count(*) FROM itens").fetchone()[0]
        n_pedidos = con.execute("SELECT count(*) FROM pedidos").fetchone()[0]
        n_linhas = con.execute("SELECT count(*) FROM itens_pedido").fetchone()[0]
        receita = con.execute("SELECT round(sum(total), 2) FROM pedidos").fetchone()[0]
        periodo = con.execute(
            "SELECT min(data_hora::date), max(data_hora::date) FROM pedidos"
        ).fetchone()
    finally:
        con.close()

    print(f"Banco gerado em: {caminho}")
    print(f"Periodo: {periodo[0]} a {periodo[1]}")
    print(f"Itens: {n_itens} | Pedidos: {n_pedidos} | Linhas de item: {n_linhas}")
    print(f"Receita total: R$ {receita:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))


if __name__ == "__main__":
    main()
