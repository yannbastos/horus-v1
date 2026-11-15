# Avaliação de provedores para o Hermes real — subsídio à decisão (HOR-116)

Issue de gestão: [HOR-116](https://github.com/yannbastos/horus-management/issues/104). Serve à decisão **pendente do idealizador** da fase F1 de [plano-hermes-real.md](plano-hermes-real.md) (provedor/modelo, preço por token, teto monetário no provedor) e ao item **C8** do [checklist pré-inferência](checklist-pre-inferencia.md). **Não configura nada, não cria conta nem chave e não habilita inferência.**

Metodologia: pesquisa pública feita em **14/11/2026** em páginas oficiais; nenhum dado do usuário ou do projeto foi enviado. **[F]** = lido na fonte oficial citada; **[S]** = fonte secundária ou resumo de busca (reler a oficial antes de decidir); **[N]** = não consegui confirmar. Preços e termos mudam: reler a fonte no dia da contratação. Nota de transparência: quem escreve é um modelo da Anthropic; a comparação usa só critérios abaixo e as fontes citadas.

## 1. Resumo

| Critério | Anthropic | OpenAI | Google (Gemini API) | Mistral (La Plateforme) | Local (Ollama) |
|---|---|---|---|---|---|
| Treino com dados da API | **Não por padrão** (termos comerciais) [F/S]; exceção: feedback enviado ou opt-in | **Não**, salvo opt-in [F] | **Pago: não**; **gratuito: sim**, com revisão humana [F] | Não, no plano Scale; exceção: modelos Labs/Preview [S] | n/a (nada sai da máquina) |
| Retenção padrão | Entradas/saídas apagadas em até **30 dias** (exceções legais/política de uso) [F] | Logs de abuso até **30 dias**; estado de aplicação varia por endpoint [F] | "Período limitado" para abuso, **duração não informada** [F] | 30 dias de abuso [S] | Local |
| Zero retenção | Por **acordo** com a Anthropic (como obter: [N]) [F] | **ZDR/MAM** sob aprovação prévia e contato com vendas [F] | [N] | ZDR: fontes divergentes (plano Scale vs sob aprovação) [S] | n/a |
| Região | Parâmetro `inference_geo` (`us`/`global`) [F]; subprocessadores: [N] | Guia de residência de dados com regiões por elegibilidade [F]; lista: [N] | Pode armazenar/processar "em qualquer país" com instalações do Google; lista de regiões não incluída; subprocessadores via DPA [F] | **UE por padrão** (endpoint EUA opcional) [S]; subprocessadores com SCC [S] | n/a |
| Teto de gasto no provedor | **Limite mensal por organização e por workspace dedicado**, abaixo do teto do nível; workspace padrão não aceita teto próprio [F] | **Hard limit** por organização/projeto se **Enforce a hard limit** estiver ativado; alerta sozinho não bloqueia [F] | **Spend cap** do Cloud Billing por projeto/serviço elegível; cap por projeto no AI Studio é **experimental**, indisponível para contas Invoiced/Offline [F] | **Limite mensal por Workspace**, ≤ limite da organização; sem cap por padrão [F] | n/a |
| Ao estourar | HTTP **400** `invalid_request_error` no limite próprio; **429** `enforced_spend_limit_reached` no teto do nível; **sem `retry-after`**; volta no mês seguinte ou ao aumentar [F] | **429** `…_spend_limit_exceeded`; **não é instantâneo** (pode passar um pouco) [F] | Pausa novo uso até o fim do período ou até suspender o teto; **não é instantâneo**; o excedente é cobrado [F] | Acesso do Workspace suspenso até o mês seguinte ou aumento [F] | n/a |
| Latência de bloqueio | Prazo máximo não confirmado [N] | Não instantâneo; sem prazo numérico confirmado [F/N] | AI Studio: cerca de **10 min**, experimental; Cloud Billing: não instantâneo [F] | Prazo máximo não confirmado [N] | n/a |
| Compatível com o Hermes | Nativo (`ANTHROPIC_API_KEY`) [F] | Nativo (`openai-api`) [F] | Nativo (`gemini`) [F] | Só como endpoint OpenAI-compatível personalizado [F] | Endpoint personalizado local; contexto ≥ 64 mil tokens exigido pelo Hermes [F] |

Fontes: [Anthropic – limites](https://platform.claude.com/docs/en/api/rate-limits), [Anthropic – retenção](https://privacy.claude.com/en/articles/7996866-how-long-do-you-store-my-organization-s-data), [Anthropic – treino](https://support.anthropic.com/en/articles/7996868-is-my-data-used-for-model-training), [Anthropic – termos comerciais](https://www.anthropic.com/legal/commercial-terms); [OpenAI – seus dados](https://developers.openai.com/api/docs/guides/your-data), [OpenAI – limites de gasto](https://developers.openai.com/api/docs/guides/spend-limits); [Google – termos da Gemini API](https://aistudio.google.com/docs/terms), [Google – spend caps](https://docs.cloud.google.com/billing/docs/how-to/budgets-spend-caps); [Mistral – limites de uso](https://docs.mistral.ai/admin/workspaces/usage-limits), [Mistral – treino](https://help.mistral.ai/en/articles/323757-do-you-use-my-user-data-to-train-your-artificial-intelligence-models); [Hermes – provedores](https://hermes-agent.nousresearch.com/docs/integrations/providers). Todas lidas em 14/11/2026.

## 2. Preços (USD por milhão de tokens, tarifa padrão, texto)

| Provedor / modelo | Entrada | Saída | Observação (fonte, 14/11/2026) |
|---|---|---|---|
| Anthropic Haiku 5.5 | 0,10 | 0,50 | até 100 mil tokens de prompt; acima, 0,50 / 2,50 [F, [preços](https://platform.claude.com/docs/en/about-claude/pricing)] |
| Anthropic Sonnet 5.5 | 2 | 10 | [F, mesma página] |
| Anthropic Opus 5.5 | 4 | 20 | [F] |
| OpenAI gpt-6-luna | 0,10 | 0,50 | [F, [preços](https://developers.openai.com/api/docs/pricing)] |
| OpenAI gpt-6.1-terra | 1 | 5 | [F] |
| OpenAI gpt-5.4-mini | 0,75 | 4,50 | [F] |
| Google Gemini 3.5 Flash-Lite | 0,30 | 2,50 | [F, [preços](https://aistudio.google.com/docs/pricing)]; paga |
| Google Gemini 3.8 Flash | 0,75 até 31/12/2026, depois 1,50 | 3,75 até 31/12/2026, depois 7,50 | [F]; **preço muda em 01/01/2027** |
| Mistral Large | 0,5 | 1,5 | única linha de API na página de preços [F, [preços](https://mistral.ai/pricing)]; Small 4 e Medium 3.5: [N] |
| Ollama local | 0 | 0 | custo é hardware/energia; modelo e licença escolhidos pelo operador [N: qualidade/português] |

Impacto no gate do Hórus: a reserva por execução é `HORUS_MAX_TOKENS_PER_RUN × max(preço de entrada, preço de saída) / 1.000.000`. Com o teto aprovado de 16.384 tokens, a reserva por execução fica em ≈ **US$ 0,008** (Haiku 5.5), **0,025** (Mistral Large), **0,041** (Gemini 3.5 Flash-Lite), **0,074** (gpt-5.4-mini), **0,082** (gpt-6.1-terra) e **0,164** (Sonnet 5.5). Todos cabem nos tetos atuais (US$ 5 total, US$ 1/dia). Os preços são **configuração do operador** (`HORUS_PRICE_INPUT_USD_PER_MTOK`/`OUTPUT`), sem tabela embutida; o gate recusa sem eles.

## 3. Compatibilidade com o contrato do Hermes

- O Hórus fala só com o Hermes em loopback; o provedor é configurado **no perfil Hermes**, não no Hórus (por isso o teto no provedor, C2, é externo ao backend). O corpo de `/v1/runs` aceita `model`/`provider` opcionais, mas o plano mantém o provedor fixado no perfil e o [gateway de saída](politica-destinos-e-ameacas.md) não audita o destino atrás do Hermes (limite conhecido).
- O contrato exige **saída JSON validada** pelo Hórus (schema, fontes permitidas, `task_id`); saída inválida é rejeitada, nunca consertada. Qualquer modelo que siga instruções em português e emita JSON estrito serve; **qualidade real por modelo: [N]** até a primeira execução com dataset fictício (F3).
- O Hermes exige **≥ 64 mil tokens de contexto** para uso com ferramentas [F]; o perfil do Hórus fica **sem ferramentas** (ver [segurança do Hermes](seguranca-hermes.md)). Se o Hermes recusa janelas menores ou exige suporte a ferramentas mesmo com o perfil mínimo: [N], testar no F2.
- **Cuidado com o Ollama:** a biblioteca lista modelos "cloud" que rodam fora da máquina [F]; usar só modelos locais (`ollama pull`), com `OLLAMA_CONTEXT_LENGTH` ≥ 64000 (padrão em GPU < 24 GB é 4.096 [F]). Hardware, velocidade e qualidade em português neste PC: [N].
- Quando o limite do provedor estoura, o erro chega ao Hórus **via Hermes**; o mapeamento HTTP 400/429 → `failed` sem retentativa já existe no cliente (`workflow_hermes`), mas o texto exato que o Hermes repassa: [N].

## 4. Recomendação (a decisão é do idealizador)

**Primeira escolha: Anthropic, com chave e workspace dedicados ao Hórus.** Motivos: treino desligado por padrão e retenção curta e documentada (30 dias, ZDR por acordo); **teto mensal próprio por workspace, enforçado pelo provedor**, com erro explícito e sem retentativa útil (casa com a regra "sem retentativa automática"); suporte nativo no Hermes; reserva por execução muito abaixo dos tetos. Modelo para o primeiro ensaio: **Haiku 5.5** (custo mínimo; se a qualidade do roteiro/análise não bastar, subir para **Sonnet 5.5**). Configuração sugerida (o operador faz, não este documento): workspace só do Hórus, limite mensal igual ao orçamento total do Hórus (US$ 5) ou menor, preços de entrada/saída copiados da tabela acima, revisão do ZDR se o envelope deixar de ser fictício.

**Alternativas, em ordem:**
1. **Ollama local** — zero saída de dados e custo, ideal para os ensaios F2/F3 e como contingência; risco: qualidade/português/64k de contexto e desempenho [N]. Recomendado **em paralelo** como teste sem custo antes de gastar.
2. **OpenAI** — sem treino por padrão e ZDR sob aprovação; limite duro documentado por organização/projeto quando **Enforce a hard limit** está ativado, **não instantâneo**; alerta isolado permite continuar o tráfego. Ensaio de corte dependerá de autorização própria.
3. **Google Gemini API** — usar **somente o nível pago** (o gratuito treina com os dados e permite revisão humana) [F]; spend cap existe, mas **não é instantâneo** e o excedente é cobrado; localização "em qualquer país" e retenção de abuso sem prazo informado pesam contra a revisão de privacidade (C8); preços de modelos Flash sobem em 01/01/2027.
4. **Mistral** — UE por padrão (bom para LGPD/GDPR) e limite por workspace; no Hermes entra só como endpoint personalizado e os preços/ZDR dos modelos atuais ficaram **[N]**.

## 5. O que fica pendente para a decisão

| Item | Quem | Observação |
|---|---|---|
| Escolher provedor e modelo | idealizador | usar a seção 4 como ponto de partida |
| Criar conta/chave dedicada e **definir o teto no provedor** (C2) | idealizador | nenhuma conta foi criada aqui; um corte "não instantâneo" exige margem (US$ < teto do Hórus) |
| Copiar preços de entrada/saída para as variáveis do Hórus | idealizador | releia a página de preços no dia |
| Revisar retenção/treino/subprocessadores com o texto vigente (C8) | idealizador | itens [N]: ZDR da Anthropic (como pedir), regiões/subprocessadores, ZDR da Mistral, retenção de abuso do Google |
| Ensaio F3 (dataset fictício, teto mínimo) | após autorização | confirmar formato real do erro de limite repassado pelo Hermes e qualidade do JSON |

## 6. Revisão independente financeira — Codex, 15/11/2026

Revisão documental pública, sem conta/chave, chamada de inferência ou teste de cobrança. O comentário anterior de pausa não validava fontes; esta revisão é nova e limitada a preços, controles financeiros e estimativas. Não revalida toda a matriz de privacidade nem constitui escolha do provedor.

**Preços:** confirmados os valores de texto padrão da seção 2 nas páginas oficiais de [Anthropic](https://platform.claude.com/docs/en/about-claude/pricing), [OpenAI](https://developers.openai.com/api/docs/pricing), [GPT-5.4 mini](https://developers.openai.com/api/docs/models/gpt-5.4-mini), [Google](https://aistudio.google.com/docs/pricing) e [Mistral](https://mistral.ai/pricing/), consultadas em 15/11/2026. Na OpenAI, distinguir Standard de Flex/Batch/Fast e eventuais adicionais regionais. No Gemini, os valores incluem tokens de thinking; a promoção Flash termina em 31/12/2026. O exemplo público Mistral Large confirma 0,50/1,50, mas não fixa ID/versionamento para futura configuração.

**Correções e limites da evidência:**

- [Anthropic — limites](https://platform.claude.com/docs/en/api/rate-limits): teto próprio retorna 400; teto do nível retorna 429 `enforced_spend_limit_reached`, sem `retry-after`, até o próximo mês ou aumento. Workspace padrão não admite limite de gasto próprio. **Latência máxima de bloqueio e garantia de nenhum excedente: [N]**; a documentação consultada não quantifica. A recomendação da seção 4 não demonstra corte mais rápido que os concorrentes.
- [OpenAI — spend limits](https://developers.openai.com/api/docs/guides/spend-limits): a distinção correta é **alerta versus enforcement habilitado**, não uma divergência técnica comprovada entre projetos. Com enforcement, 429 `organization_spend_limit_exceeded` ou `project_spend_limit_exceeded`; pode haver pequeno excedente durante propagação. Aumento/remoção libera após propagação; sem isso, reinicia no ciclo mensal. **Prazo máximo numérico: [N]**. Os relatos secundários anteriores não substituem esta fonte oficial.
- [Google — Cloud Billing](https://docs.cloud.google.com/billing/docs/how-to/budgets-spend-caps): orçamento somente de alerta difere de spend cap; este pausa uso novo do serviço elegível, mas conclui requisições em andamento e cobra excedentes; não é instantâneo. Não confundir atraso dos relatórios (até mais de 24 h) com prazo de bloqueio. [AI Studio — billing](https://aistudio.google.com/docs/billing#project-spend-caps): cap experimental por projeto admite excedentes por latência de **cerca de dez minutos**, inclusive tarefas longas; não é teto absoluto de cobrança. O teto do nível pertence à conta de faturamento e agrega projetos.
- [Mistral — workspace limits](https://docs.mistral.ai/admin/workspaces/usage-limits.md): confirmado bloqueio do acesso à API do workspace até novo mês ou aumento por administrador; cap é opcional e usa a moeda da organização. **Latência máxima, excedentes em trânsito e código HTTP: [N]** nesta fonte. Não prometer corte instantâneo.

### Reserva conservadora para 16.384 tokens

A fórmula já descrita confere com `security.cost_reservation`. Trata-se de **reserva do gate**, não de consumo medido nem de garantia de valor final da fatura. Para entrada + saída somando no máximo 16.384 tokens, sem ferramentas, armazenamento, adicionais ou tarifas diferenciadas, o custo textual é `(entrada × preço_entrada + saída × preço_saída) / 1.000.000`, limitado pela reserva abaixo. Cache tarifado de forma diferente, execução que ultrapasse o teto e cobranças externas exigem contabilização própria. Caber nos limites de US$ 1/dia e US$ 5 total pressupõe saldo livre; não autoriza gasto.

| Modelo / modalidade da seção 2 | Reserva em USD (sem arredondar para baixo) |
|---|---:|
| Haiku 5.5 (prompt até 100 mil) / gpt-6-luna | 0,008192 |
| Sonnet 5.5 | 0,163840 |
| Opus 5.5 | 0,327680 |
| gpt-6.1-terra | 0,081920 |
| gpt-5.4-mini | 0,073728 |
| Gemini 3.5 Flash-Lite | 0,040960 |
| Gemini 3.8 Flash até 31/12/2026 | 0,061440 |
| Gemini 3.8 Flash a partir de 01/01/2027 | 0,122880 |
| Mistral Large (exemplo público) | 0,024576 |

Ollama não cobra tokens locais, mas tem custo de hardware/energia. O gate exige preços positivos; zero na tabela comparativa não significa configuração aceita pelo gate. A escolha, aceitação de riscos e qualquer ensaio pago continuam pendentes do idealizador.
