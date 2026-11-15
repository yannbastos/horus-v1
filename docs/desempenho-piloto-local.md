# Medição do piloto local — 14/11/2026

`scripts/measure-local-pilot.py` observa uma jornada real sem alterar a produção: usa TEMP/TMP e banco temporários, amostra a árvore de processos com psutil e lê eventos de etapa e artefatos locais. As amostras não acrescentam argumentos completos dos processos nem texto da requisição de narração; o log do comando filho e os metadados dos artefatos ficam no relatório local. Requer o extra `measure` do backend e as dependências locais dos lockfiles; não provisiona modelos/ferramentas em runtime.

Configure `HORUS_KOKORO_PYTHON`, `HORUS_KOKORO_MODEL_DIR`, `HORUS_FFMPEG`, `HORUS_ALIGNMENT_MODEL` e `HORUS_PILOT_FACTORY` com instalações locais já existentes. Com `HORUS_E2E_STRICT=1` e `HORUS_E2E_FINAL=1`, execute:

```powershell
backend/.venv/Scripts/python.exe scripts/measure-local-pilot.py --output "$env:TEMP/pilot-final.json" -- node frontend/scripts/e2e-backend.mjs run --grep "Final 1080x1920"
```

Para os três cenários regulares, use `--grep-invert "Final 1080x1920"`. O comando filho mantém seu código de saída. O JSON contém eventos, CPU acumulada e RSS máximo por processo, pico de RSS somado da árvore, tempo efetivo entre amostras, CPU do observador e metadados dos artefatos. A classificação distingue síntese, alinhamento, FFmpeg de legenda, QC e thumbnails. As consultas de artefato usam caminhos fixos e não percorrem node_modules. Processos muito curtos podem escapar da amostragem; RSS somado inclui páginas compartilhadas e não equivale a memória privada exclusiva.

O padrão de amostragem final é 1 s, configurável por `--interval` entre 0,1 e 5 s. Os ensaios desta rodada usaram 0,2 s solicitado, mas a jornada 3/3 teve média efetiva de 0,526 s e custo do observador de 332,94 s de CPU em 679,06 s de parede; o padrão foi reduzido em frequência para limitar esse custo. Não foi reivindicado ganho de desempenho do app com essa mudança do observador.

## Referência Final real

Roteiro sintético de quatro seções, 193 palavras, Kokoro `pf_dora`, áudio de 73,225 s, MP4 1080×1920/30 fps de 74,033 s e 2221 frames. Código de produção inalterado; sem alteração de preset, concorrência, modelo ou número de processos.

| Etapa | Tempo (s) | Fonte |
|---|---:|---|
| Narração e preparação anterior ao alinhamento | 141,963 | eventos do backend |
| Alinhamento | 109,447 | eventos do backend |
| Legendas anteriores ao render | 0,014 | eventos do backend |
| Render completo, incluindo preparação/auditoria | 390,704 | eventos do backend |
| Fábrica/Remotion dentro do render | 248,859 | validation.json |
| FFmpeg/libass dentro do render | 35,844 | caption-overlay.json |
| Validações/imagens auxiliares dentro do render interno | 15,859 | diferença dos relógios internos |
| QC | 10,388 | eventos do backend |
| Thumbnail PNG, extração isolada do mesmo MP4 | 0,188 | relógio externo, comando da publicação |
| Thumbnail JPEG, conversão do PNG | 0,081 | relógio externo, comando da publicação |

Da entrada de narração à entrada de revisão: 652,515 s; jornada Final completa na interface: 736,195 s, aprovada. O render tem ainda 90,142 s fora do relógio interno: inclui cópia/preparação, carga do subprocesso, auditoria e finalização; não foi atribuído integralmente à cópia sem um relógio específico. QC `warn`, sem falhas bloqueantes. Hash do MP4 preservado e igual em arquivo, validação e QC: `7c15f651b9872f4d27ba70c3d6ae5cf728f46e738a7e33b78f4a050b4a497d34`.

Pico amostrado de RSS somado: 3,18 GiB; pico de CPU da árvore: 904% de um núcleo lógico, aproximadamente 56,5% dos 16 lógicos da máquina. Cada segmento Kokoro abriu um processo e carregou o modelo: durações amostradas de aproximadamente 59,7 / 23,2 / 24,3 / 20,7 s, com 16,9 / 16,3 / 15,9 / 15,3 s de CPU e pico de 1,74–1,81 GiB por processo. Esses tempos incluem inicialização/saída e têm precisão limitada pelo observador; não são duração exata de inferência.

## Limites e decisão

Havia carga concorrente de instalação/antivírus. O primeiro coletor exploratório também varria dependências e consumia CPU relevante; isso foi corrigido no observador, com teste que impede a regressão. Os relógios de etapa vêm do backend, mas o custo do observador e a concorrência afetam a execução medida: não é benchmark isolado nem prova de regressão contra o histórico de cerca de 207 s, cujos insumos/configuração não foram demonstrados equivalentes.

Além da referência Final 1/1, a jornada padrão real passou 3/3 em 11,3 min, com três MP4s 540×960 de 22,333 / 14,933 / 12,900 s, todos com QC sem falha bloqueante. O resultado serve como prova funcional; são cargas diferentes do Final e não uma comparação de aceleração.

Não foi aplicada otimização ao app sem prova de equivalência: reaproveitar Kokoro exige verificar áudio/RNG e isolamento, compartilhar dependências de render altera isolamento gravável, e trocar preset altera o MP4. QC/thumbnail não dominam esta referência; a decodificação dos mesmos 742 PNGs variou de 1,010 a 4,391 s sob carga, portanto uma mudança nesse ponto não justificaria afirmar ganho no vídeo inteiro. Os arquivos de narração permaneceram somente leitura. Próximos experimentos devem reutilizar os mesmos insumos, controlar carga e comparar hashes/frames/áudio/QC antes de habilitar mudança no primeiro vídeo.
