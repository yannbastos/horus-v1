# Retomada de pipeline longo sem repetir gastos (HOR-096) — desenho

**Estado: desenho. Nada aqui está implementado.** Este documento não altera o fluxo de produção nem autoriza implementá-lo; a implementação depende de decisão do Maestro depois do teste do primeiro vídeo. A rede de segurança do comportamento atual está em `backend/tests/test_pipeline_resume_characterization.py`.

## 1. Comportamento atual (verificado no código e nos testes)

| Situação | Hoje |
|---|---|
| Backend/worker cai com a produção em `running` ou `cancel_requested` | Na subida, o job vira `interrupted` com `failure.code = production_interrupted` e a etapa (`narration`, `align`, `render`…) preservada. Nenhum worker é relançado |
| Arquivos da tentativa | Ficam intactos na pasta da produção, **não são registrados** como artefatos (`artifacts == []`) |
| `POST /v1/jobs/{id}/retry` em `production.run` | 409: "exige conciliação; crie nova produção explicitamente" |
| Mesma `Idempotency-Key` | Devolve a produção interrompida (200); não inicia nada |
| Nova produção (outra chave) | Pasta nova, worker inteiro de novo (`prepare` → narração → cenas → alinhamento → legendas → render → qualidade); nenhum arquivo da tentativa anterior é copiado ou consultado |
| Pasta de tentativa já existente quando o job executa | Bloqueia (`blocked`, "Saída de tentativa anterior presente"); nunca sobrescreve nem continua |
| Narração no `local_pilot` | Uma única chamada de síntese para todas as seções (`prepare` → `synthesize`); WAV por seção preexistente não é consultado |
| Plano aprovado | Determinístico: mesmas entradas geram o mesmo `plan` |
| Sucesso | Artefatos e `succeeded` são gravados na mesma transação |

Resultado: toda interrupção custa o pipeline inteiro. Hoje isso é aceitável (tudo local e gratuito). Passa a ser caro quando houver provedor pago (ElevenLabs) ou inferência do Hermes por etapa.

## 2. O que pode ser reaproveitado após interrupção

Princípio: reaproveitar **somente uma saída completa, imutável e comprovadamente derivada das mesmas entradas**. Uma etapa é reaproveitável quando é determinística o bastante para que repetir seja desperdício, ou cara o bastante para que o risco controlado compense.

| Etapa | Reaproveitável? | Condição |
|---|---|---|
| **Narração por segmento** (WAV por seção) | **Sim, a de maior valor.** Hoje é a chamada paga futura (ElevenLabs) e a mais lenta no Kokoro | O segmento só vale se texto normalizado, provedor, `voice_id`, modelo/versão, parâmetros e idioma forem idênticos e o WAV passar na validação de formato (`wav_info`: não vazio, não truncado) |
| **Normalização de loudness** | Sim, derivada | Hash do WAV original + versão/hash do ffmpeg + parâmetros |
| **Alinhamento** (`word-timings.json`) | Sim | Hash do áudio normalizado + modelo de alinhamento (id/versão/hash) + texto; o contrato já valida `narration_sha256` |
| **Cenas/imagens locais** | Sim, barato | Hash do texto/layout + versão do gerador; baixa prioridade |
| **Legendas** (SRT/overlay) | Derivadas: regerar, não reaproveitar | São baratas e dependem do alinhamento e da fonte |
| **Render** | Só se **todas** as entradas a montante forem as mesmas e o vídeo parcial **nunca** for continuado: reaproveita-se apenas um MP4 completo com validação física (frames de áudio/vídeo, duração, hash) | MP4 parcial ou sem `validation.json` coerente nunca é reaproveitado |
| **QC / relatório de qualidade** | Não reaproveitar: sempre medir de novo sobre o vídeo final | O relatório referencia o hash do vídeo; sem o mesmo hash, não vale |
| **Revisão e aprovação humana** | Nunca | Ver seção 5 |

## 3. Como provar que o reaproveitamento é seguro

Cada etapa concluída grava ao lado da saída um **recibo de etapa** (JSON atômico, escrito depois da saída, com `fsync`/`os.replace`), e só o recibo torna a saída elegível:

```
{ "schema_version": 1,
  "stage": "narration_segment", "segment_id": "section-3",
  "contract_version": <versão do contrato de produção/plano>,
  "tool": {"id": "kokoro", "version": "...", "model_sha256": "..."},
  "inputs":  {"text_sha256": "...", "voice_id": "...", "params_sha256": "...", "upstream": ["<sha256 dos recibos anteriores>"]},
  "outputs": [{"path": "section-3.wav", "size": N, "sha256": "..."}],
  "provenance": {"origin": "...", "license": "...", "generated_at": "..."},
  "paid": false, "cost_ref": null }
```

Regras de elegibilidade (todas, senão repete a etapa):

1. **Hash de entradas idêntico:** recalculado a partir do plano aprovado (já determinístico e validado contra o roteiro) e dos recibos a montante. Mudou uma palavra, voz, modelo ou parâmetro ⇒ novo hash ⇒ não reaproveita.
2. **Hash de saída confere:** o arquivo em disco tem o tamanho e o SHA-256 do recibo e passa na validação de formato da etapa.
3. **Versão do contrato e da ferramenta:** `contract_version` e `tool` do recibo iguais aos atuais. Versão desconhecida ou mais nova ⇒ recusa.
4. **Proveniência preservada:** o recibo carrega origem/licença; a cadeia de `asset-provenance.json` do resultado final é reconstruída dos recibos, não inventada.
5. **Encadeamento:** `upstream` referencia os recibos anteriores; um elo quebrado invalida tudo a jusante.
6. **Imutabilidade:** a pasta da tentativa original não é alterada. A retomada cria uma **nova tentativa** (nova pasta/novo `attempt`) que **copia ou referencia por hash** apenas as saídas elegíveis; a auditoria mostra `reused: [{stage, sha256, from_attempt}]`.
7. **Sem links, sem caminhos fora da pasta** (`confined`, já usado hoje) e sem arquivo com formato de credencial.

A decisão de reaproveitar é exibida ao operador antes de executar ("reaproveita 7 de 12 segmentos de narração; refaz alinhamento e render") e registrada na trilha da produção.

## 4. O que nunca reaproveitar

- Qualquer arquivo sem recibo, com recibo inválido, de contrato/ferramenta diferente ou com hash divergente.
- MP4 parcial ou sem validação física; saída de um worker que terminou com erro, `blocked` ou código ≠ 0.
- WAV vazio, truncado ou corrompido (já recusado por `wav_info`).
- QC, `quality-report`, aprovação e revisão humana anteriores (pertencem a um vídeo específico por hash).
- Qualquer coisa depois de mudança do roteiro aprovado, da seleção de voz/provedor, da qualidade (`draft`/`final`) ou do plano.
- Respostas de provedor pago ou do Hermes **sem** recibo de custo/transmissão correspondente (não há como provar que existiram nem quanto custaram).
- Saída de uma tentativa que ainda possa estar sendo escrita (worker vivo/lock do worker não liberado).
- Credenciais, tokens e dados do operador: nunca entram em recibos.

## 5. Evitar gasto em provedores pagos futuros e ligação com os checkpoints humanos

O gasto acontece na **unidade de síntese** (segmento) e não na produção inteira. O desenho:

- **Recibo de custo antes do gasto:** a chamada paga só é feita depois de gravar uma intenção (`segment_id`, `inputs_sha256`, provedor, estimativa) e, ao terminar, o recibo da etapa. Se o processo cair entre a resposta e a gravação, o recibo de intenção sem saída marca **gasto possivelmente feito e não recuperado**: a retomada trata como "incerto" e pede decisão humana, em vez de repetir silenciosamente.
- **Retomada = zero chamadas repetidas para segmentos com recibo válido.** O custo marginal de uma interrupção passa a ser, no máximo, o segmento em curso.
- **Chave de idempotência por segmento** (`inputs_sha256`), propagada ao provedor quando ele suportar, para que uma repetição após falha de rede não cobre duas vezes.
- **Teto e aprovação:** a estimativa do que ainda precisa ser sintetizado (segmentos sem recibo) é comparada ao teto financeiro configurado; acima dele, a retomada não prossegue.
- **Linha retida (Hermes: checkpoints humanos #95, gateway de saída #99, auditoria de transmissões #103):** a retomada **não** contorna esses mecanismos. Um checkpoint humano já aprovado vale **somente** para o hash de entradas que ele aprovou (roteiro, provedor, estimativa, teto); se a retomada mudar qualquer um deles, o checkpoint é inválido e exige nova aprovação. Antes de qualquer chamada paga ou de saída HTTP, a barreira do backend continua obrigatória; recibos de etapa não substituem checkpoint, e checkpoint não substitui recibo. Cada reaproveitamento e cada gasto aparecem na auditoria append-only de transmissões. Nada disso habilita provedor pago, Hermes real ou publicação por si só.
- Depende da integração prévia da linha retida (`yannbastos/integ-hermes-line-v3`, após o teste do primeiro vídeo): a ligação com checkpoints e transmissões só pode ser implementada depois dela. Até lá, a implementação possível é apenas local e gratuita.

## 6. Ordem de implementação sugerida (futura, depende de aprovação)

1. Recibo de etapa + validação (módulo isolado, sem mudar o fluxo), com testes de hash, contrato, adulteração e encadeamento.
2. Narração por segmento com recibo (local: Kokoro/Piper/SAPI) e `prepare` capaz de pular segmentos elegíveis; o worker passa a escrever recibos.
3. Nova tentativa que reaproveita por hash + exibição ao operador + auditoria (`reused`).
4. Alinhamento/normalização; render só com MP4 completo validado.
5. Ligação com custo/checkpoints/transmissões quando a linha retida estiver na `main` e houver provedor pago autorizado.

Cada passo muda os testes de caracterização de forma explícita (ver o cabeçalho do arquivo de testes). Mudança de contrato da API (por exemplo, `POST .../productions/{id}/resume`) é aditiva, com atualização do snapshot e aprovação prévia (AGENTS.md: alterar contratos exige aprovação).

## 7. Riscos e perguntas em aberto

- Determinismo: Kokoro/ffmpeg podem produzir bytes diferentes em versões diferentes — por isso a versão da ferramenta entra no hash de entradas e o reaproveitamento é por recibo, não por "regerar e comparar".
- Crash entre saída e recibo: a saída sem recibo é lixo e nunca é aproveitada; custo possível apenas do segmento em curso.
- Espaço em disco: reaproveitar por cópia duplica WAVs; referência por hash exige armazenamento endereçado por conteúdo (decisão futura).
- Retenção: recibos e saídas de tentativas antigas entram na política de retenção e de exclusão de dados (HOR-117).
