# Kokoro persistente — experimento de equivalência

Data: 15/11/2026. Escopo HOR-087: reduzir recarga do Kokoro por segmento sem mudar o primeiro vídeo. **Entrega desta rodada: medição e desenho; nenhum worker persistente ou opção foi implementado no produto**, porque o critério de equivalência falhou. `processes.py`, contratos HTTP, snapshot OpenAPI e banco permanecem inalterados.

## Método e resultado

Base inspecionada: `6ab56dd`. O worker atual instancia `KModel` e `KPipeline` a cada chamada de síntese. Experimento externo ao código de produção: duas chamadas originais separadas com o mesmo texto sintético de 20 palavras e uma chamada experimental com duas sínteses desse texto, reutilizando modelo/pipeline. Voz `pf_dora`, velocidade 1, mesma conversão PCM, pesos locais verificados pelos hashes existentes antes/depois. Kokoro 0.9.4, Torch 2.8.0+cu126, NumPy 2.5.4 e Misaki 0.9.4; seleção automática de dispositivo preservada. Não foi introduzida semente aleatória.

O coordenador autorizou TTS offline local e o contrato interno batch, com tolerância **máxima de 1 LSB PCM e parâmetros/frames idênticos**. Os quatro WAVs têm 148.800 frames (6,2 s), mono, PCM16, 24 kHz. Todos os hashes diferem.

| Comparação | Diferença máxima absoluta (LSB) | Amostras diferentes | Equivalente |
|---|---:|---:|---|
| Original 0 × original 1 | 4.076 | 135.717 | Não |
| Original 0 × persistente 0 | 4.555 | 135.498 | Não |
| Original 1 × persistente 1 | 4.832 | 135.182 | Não |

A versão instalada do vocoder usa `torch.randn_like` em `istftnet.py`; `.eval()` não elimina essas operações. O worker original não fixa RNG. A divergência já existe entre chamadas originais, portanto não se pode atribuí-la integralmente à persistência, nem afirmar equivalência pelo número de frames. Não foi alargada a tolerância nem alterada a narração para obter aprovação.

Medição com `scripts/measure-local-pilot.py`, intervalo 1 s: original 0 **49,783 s**, original 1 **15,532 s**, persistente com duas sínteses **15,617 s**. Relógios incluem carga/saída e somente o persistente sintetiza duas vezes; a ordem não foi alternada, houve aquecimento e não há réplicas suficientes. Esses números sugerem um candidato para investigação, **não um ganho comprovado na produção**. Invólucro 82,658 s, pico RSS somado 1.949.208.576 bytes, CPU máxima 118,98% de um núcleo lógico; observador 3,5625 s de CPU, intervalo médio efetivo 1,033 s. RSS compartilhada é somada e processos curtos podem escapar.

O observador escreve `command_kind=real_journey` incondicionalmente; neste ensaio esse campo **não representa jornada real**: o comando filho é o experimento de síntese, sem backend, MP4, alinhamento ou QC. Não foi editado o observador para mascarar essa limitação.

## Desenho proposto, ainda não implementado

1. Ativar exclusivamente com `HORUS_KOKORO_PERSISTENT=1`, propagada explicitamente pelas allowlists do processo de produção e do helper de narração. Ausente, `0` ou outro valor mantém o fluxo atual. Preview permanece por chamada; SAPI/Piper não mudam.
2. O `production_worker` de narração usa uma única operação batch somente para Kokoro nessa produção. Request interno aditivo mantém `text`/`output` do modo antigo e acrescenta `sections: [{text, output}]` no batch, preservando `voice_id`/`model_dir`. Não há endpoint, migração ou mudança de responsabilidade da fábrica/Hermes. Antes de implementar, fixar o formato em teste de contrato interno; snapshot OpenAPI deve continuar idêntico.
3. Um processo externo `-I`, um modelo e um pipeline; sem daemon global ou reutilização entre produções. Ambiente mínimo, offline/telemetria desabilitada, bloqueio de sockets antes dos imports e hashes dos pesos/vozes antes/depois. Requests, progressos e WAVs somente no diretório temporário pertencente à produção; nada de stdin interativo ou sockets.
4. Reutilizar `OwnedProcess.start`, `communicate` e `stop`/`close` em `finally`. Drenagem começa imediatamente e permanece limitada a `OUTPUT_LIMIT` por stream; cancelamento, erro e timeout encerram a árvore pelo Job Object/grupo existente. Não há necessidade identificada de editar `processes.py`.
5. Não substituir o prazo atual de 120 s por segmento apenas por um prazo total multiplicado: isso esconderia um segmento travado. Projetar marcador de conclusão atômico por segmento, com sequência validada pelo pai, watchdog de 120 s desde o início/última conclusão e teto total de `120 × quantidade`; a drenagem continua em tarefa paralela até a saída. Só avançar prazo com conclusão validada, nunca com mensagens arbitrárias.
6. Preservar validação WAV individual (arquivo regular, sem symlink, até 200 MB, mono/PCM16/24 kHz, frames positivos e leitura integral); aplicar também limite agregado proporcional ao número autorizado de seções e limites de request. Falha invalida o lote e remove os WAVs parciais pertencentes à tentativa. Não reutilizar saídas anteriores nem fazer fallback silencioso/retry da síntese.
7. Preservar metadados de voz/modelo, normalização, alinhamento, cenas, render, QC e revisão humana. Nenhuma alteração de preset, MP4 ou licenças acompanha a otimização.

## Porta de validação e continuidade

É necessário decidir um protocolo de RNG reproduzível antes de voltar a implementar: sementes/estados capturados para ensaio pareado podem isolar o efeito da persistência, mas não provam igualdade com WAVs históricos aleatórios. Aplicar uma semente nova em produção modifica o áudio do fluxo atual e exige decisão explícita; restaurar RNG apenas no persistente não demonstra equivalência com o processo original sem seed. Alternativamente, outro critério de áudio teria de ser aprovado com justificativa, sem alterar retroativamente o limite desta rodada.

Depois dessa decisão: comparar os mesmos roteiros, vozes e dispositivo nos dois modos, repetir e alternar ordem com o observador, conferir WAVs brutos/normalizados, hashes de MP4 (ou critério visual/áudio previamente aprovado), frames e QC, e executar jornada estrita 3/3 ligada e 3/3 desligada. Testar falha intermediária, saída excessiva, WAV inválido, timeout individual/total, cancelamento e encerramento de descendentes. Somente então entregar código atrás da opção desligada por padrão.

**Nesta rodada não houve MP4/QC novos nem jornada 3/3 ligada/desligada**: o primeiro gate de áudio falhou e foi aplicado o fallback de medição/desenho previsto no despacho. A [medição anterior](desempenho-piloto-local.md) continua evidência histórica, sem ser apresentada como validação desta proposta.

Checks executados: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_measure_local_pilot.py backend/tests/test_narration_kokoro.py backend/tests/test_api_contract_snapshot.py -q`: **22 passed, 2 skipped** (testes reais condicionais sem variáveis nessa execução), um aviso de depreciação Starlette/httpx. A síntese real foi executada separadamente pelo experimento; não confundir com os dois testes pulados.

Relatório, experimento reproduzível, JSON do observador, hashes e WAVs sintéticos preservados localmente em `relatorios/RODADA-2026-11-15` (fora do repositório público), com os nomes `perf-kokoro-persistent-worker.md`, `kokoro-experiment.py`, `kokoro-equivalence.json`, `kokoro-experiment-resources.json/.log` e `kokoro-equivalence-artifacts/`.
