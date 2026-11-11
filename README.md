# Agente de Vendas para Restaurantes

Agente de IA que responde perguntas em linguagem natural sobre as vendas de um
restaurante ("qual prato vendeu menos no mes passado?") e gera resumo semanal
com recomendacoes praticas.

## Como funciona

```
Usuario (Streamlit) -> LLM com tool calling -> ferramentas Python (DuckDB)
                    -> resultado -> LLM redige o insight -> grafico Plotly
```

O LLM nao calcula nada: ele escolhe qual ferramenta consultar. Os numeros vem
sempre do banco, o que elimina alucinacao de dados.

## Rodar

```bash
pip install -r requirements.txt      # dependencias
cp .env.example .env                 # preencha LLM_MODEL e LLM_API_KEY
python data/gerar_dados.py           # cria data/vendas.duckdb (6 meses sinteticos)
streamlit run app.py                 # abre a interface
```

Se o banco nao existir, a primeira execucao do app gera os dados sozinha.

Configuracao usada hoje no `.env` (Groq, plano gratuito):

```
LLM_BASE_URL=https://api.groq.com/openai/v1
LLM_MODEL=openai/gpt-oss-120b
```

## Estrutura

| Arquivo | Papel |
|---|---|
| `data/gerar_dados.py` | Gera 6 meses de vendas sinteticas no DuckDB |
| `src/tools.py` | Ferramentas de consulta (top itens, ticket medio, horario...) |
| `src/guardrails.py` | Valida SQL livre: so SELECT, 1000 linhas, 5s de timeout |
| `src/agent.py` | Loop de tool calling e escolha do grafico |
| `src/llm.py` | Cliente OpenAI-compativel configurado por `.env` |
| `app.py` | Interface Streamlit |
| `evals/perguntas.json` | Perguntas de teste com resposta esperada |
| `evals/rodar.py` | Executa as evals e mede a taxa de acerto |

## Evals

```bash
python evals/rodar.py --detalhado
```

Verifica fatos (nome do item, valor dentro de tolerancia), nao texto exato.

## Trocar de provedor

Basta editar o `.env`:

| Provedor | LLM_BASE_URL |
|---|---|
| Gemini | `https://generativelanguage.googleapis.com/v1beta/openai/` |
| Groq | `https://api.groq.com/openai/v1` |
| Ollama (local) | `http://localhost:11434/v1` |

Para descobrir os modelos que a sua chave do Groq enxerga:

```bash
python -c "import os;from openai import OpenAI;from dotenv import load_dotenv;load_dotenv();print([m.id for m in OpenAI(api_key=os.environ['LLM_API_KEY'],base_url=os.environ['LLM_BASE_URL']).models.list().data])"
```

## Publicar para avaliadores (Streamlit Community Cloud)

GitHub Pages **nao serve** (hospeda apenas arquivos estaticos) e Cloudflare
Workers **nao roda Python/Streamlit**. O caminho natural e o Streamlit
Community Cloud: gratis para apps publicos, deploy direto do repositorio.

1. Suba o projeto em um repositorio **publico** no GitHub (o `.env` ja esta no
   `.gitignore`, nenhuma chave vai junto).
2. Em https://share.streamlit.io entre com o GitHub e clique em *Create app*.
3. Escolha repositorio, branch `main` e arquivo `app.py`.
4. Em *Advanced settings*, cole os segredos no formato do `secrets.toml`:

```toml
LLM_BASE_URL = "https://api.groq.com/openai/v1"
LLM_MODEL = "openai/gpt-oss-120b"
LLM_API_KEY = "sua-chave"
```

5. Deploy. O app gera o banco sintetico na primeira execucao e fica em
   `https://<seu-usuario>-<repo>-app.streamlit.app`.

O `src/llm.py` le primeiro as variaveis de ambiente e, se nao houver, os
segredos do Streamlit — por isso o mesmo codigo roda local e na nuvem.

Sobre Cloudflare: Workers tem suporte a Python, mas no modelo de handler do
Worker, nao como servidor Streamlit; quem roda containers e o Workers Paid
(US$ 5/mes). Para um link gratuito, Community Cloud continua sendo o caminho.

> Apps gratuitos hibernam apos ~12 horas sem acesso; a primeira visita depois
disso demora alguns segundos para acordar.
