# Agente de Análise de Vendas para Restaurantes

## Propósito
Agente de IA que responde perguntas em linguagem natural sobre as vendas
de um restaurante (ex.: "qual prato vendeu menos no último mês?") e gera
resumos semanais com recomendações acionáveis para o dono do negócio.
Público: donos de pequenos restaurantes, sem conhecimento técnico.

## Arquitetura
Usuário (Streamlit) -> LLM com tool calling -> ferramentas Python seguras
-> DuckDB -> resultado -> LLM redige insight -> gráfico Plotly.

## Estrutura
- data/gerar_dados.py : gera 6 meses de vendas sintéticas em DuckDB
- src/tools.py        : funções de consulta (vendas_por_periodo, top_itens,
                        piores_itens, ticket_medio, vendas_por_horario)
- src/guardrails.py   : valida SQL (somente SELECT, limite de linhas)
- src/agent.py        : loop de tool calling
- src/llm.py          : cliente OpenAI-compatível, configurado por .env
- app.py              : interface Streamlit
- evals/perguntas.json: perguntas de teste com resposta esperada

## Regras de código
- Python 3.11+, tipagem nos parâmetros, docstrings curtas em português.
- Código simples e legível; sem over-engineering.
- O provedor de LLM deve ser trocável via .env (LLM_BASE_URL, LLM_API_KEY,
  LLM_MODEL), compatível com Gemini, Groq e Ollama.
- NUNCA colocar chaves de API no código. Usar python-dotenv.
- Preferir ferramentas pré-definidas; SQL livre só com guardrails.
- Toda função em tools.py retorna um DataFrame ou dict serializável.
- Respostas do agente sempre em português do Brasil, tom claro e direto.

## Segurança
- Somente SELECT; bloquear INSERT, UPDATE, DELETE, DROP, ALTER, ATTACH.
- Limite máximo de 1000 linhas por consulta e timeout curto.

## Dados (tabelas)
- itens(id, nome, categoria, preco)
- pedidos(id, data_hora, canal, total)
- itens_pedido(pedido_id, item_id, quantidade, preco_unitario)