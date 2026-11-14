# Aceite do primeiro vídeo real e rastreabilidade de licenças (HOR-132 / HOR-097 / HOR-056)

Estado em 14/11/2026, sobre a `main` `cd365b1`. Issues de gestão: #56/#120 (primeiro vídeo), #85 (licenças), #119 (vozes). **Não altera o fluxo nem os textos da UI**; só registra o que o QC automático garante, o que o humano confere e as lacunas entre o guia ([piloto-local.md](piloto-local.md), “Meu primeiro vídeo”) e o QC. Tudo é demonstração (`demo_only=true`, `publishable=false`); nada aqui concede licença nem autoriza publicar.

Convenção: **[F]** fato verificado no código/teste/arquivo local; **[N]** não verificado.

## 1. Checklist do primeiro vídeo

### 1.1 O que o QC automático já garante (`av_quality.run_quality`, `quality-report.json`)

| Check | Garante | Não garante |
|---|---|---|
| `video_present`, `evidence_manifest`, `asset_hashes` | MP4 registrado, manifesto válido, SHA-256 dos insumos conferem | Que o conteúdo seja bom |
| `video_format`, `duration_narration`, `duration_video_audio` | 1080×1920 (final), duração do vídeo = áudio medido + cauda | Ritmo/edição |
| `audio_level`, `audio_clipping`, `audio_silence`, `audio_loudness` | Pico, clipping, silêncios longos, LUFS/true peak/LRA medidos | Inteligibilidade, naturalidade e pronúncia da voz |
| `word_timings`, `subtitles_vs_words`, `subtitles_vs_sections`, `script_coverage` | Cobertura do roteiro pelas legendas e desvio vs. palavras (`estimated` nunca vira “medido”) | Legenda alinhada à fala real (`speech_aligned=false` no piloto) |
| `visual_black_frames`, `visual_freeze` | Sem preto ≥0,5 s nem quadro congelado ≥4 s | Que a imagem combine com a narração |
| `asset_provenance` | Proveniência válida, cruzada com os hashes; **warn** se algum asset não estiver verificado por humano | Que a licença seja de fato válida (só registra a decisão humana) |

`status`: `fail` em `blocking_errors`; `warn` em `warnings`; `not_run` quando não há relatório (não é aprovação).

### 1.2 O que o usuário confere no teste humano

Marque cada item como executado/aprovado/reprovado/não executado; item não executado não conta como aprovação.

1. Ajustes → **Pronto para produzir?** = PRONTO (ou só o provedor de voz alternativo faltando).
2. Voz ouvida na prévia (`OUVIR PRÉVIA`) e escolhida (kokoro/pf_dora ou Maria) — **julgamento humano**: soa natural? pronúncia de números/siglas?
3. Roteiro aprovado (versão e hash do `ScriptDocument`) e 60–120 s (~150–200 palavras).
4. Produção **Final 1080×1920** concluída (≈3–4 min); sem falha, sem `FALHA NA EXECUÇÃO TÉCNICA`.
5. **Assistir o MP4 inteiro**: troca de cena coerente com a fala, legendas dentro da fala, sem corte/estouro, sem imagem confusa — **humano** (QC só mede forma).
6. **Ouvir o áudio inteiro**: sem estalos, cortes ou distorção; volume confortável.
7. QC lido: `STATUS DO QC` e checks; `asset_provenance` em AVISO é esperado até a verificação de licença.
8. Licenças: cada asset (cenas PNG, trechos de voz/narração, fonte da legenda) com **Registrar decisão humana**, só o que foi realmente conferido; `unknown` só pode ser rejeitado.
9. **Aprovar produção (hash verificado)**; exportar pacote e conferir que o ZIP contém vídeo, legendas, roteiro, `quality-report.json`, `licenses.json`, `review.json`.
10. Confirmar que continua `publishable=false`: publicação é decisão e ação manual fora do Hórus.

### 1.3 Lacunas entre o guia da UI e o QC (nenhuma corrigida na UI, por regra da tarefa)

| # | Lacuna | Evidência | Situação |
|---|---|---|---|
| G1 | O guia diz “se houver pendência de QC/licença, marque Confirmo ciência…”, mas o reconhecimento só é exigido com QC `fail`/`not_run` (`production.py`, `quality_acknowledgement_required`). **Licença pendente (`warn`) não bloqueia a aprovação.** | `production.py:292`, `test_license_reviews.py::test_approval_does_not_publish_or_change_artifacts` | **Decisão do Maestro/idealizador**: manter (aprovação ≠ publicação) ou exigir reconhecimento também em `warn` de licença. Não alterado. |
| G2 | `verified` + `commercial_use=restricted` passava como “Licenças revisadas” nos metadados e `pass` no QC. | `publishing.py`, `av_quality.provenance_check` | **Metadados corrigidos** (`licenses_pending`: só `verified`+`allowed`+humano libera). **QC (`av_quality.py`) fora do escopo**: lacuna registrada em teste `xfail(strict)` em `test_licensing_gates.py`. |
| G3 | O guia diz que o aviso `asset_provenance` “some depois da verificação”: só ocorre quando **todos** os assets (cenas, trechos de voz, narração, fonte) estão verificados, não só a voz. | `validate_asset_provenance` | Texto da UI não alterado; registrar para o redator do guia. |
| G4 | O guia manda verificar “cada asset”, mas não diz que verificar a voz **não** prova a licença dos dados de treino (Kokoro) nem a do Windows (Maria). | seção 2 abaixo | Texto da UI não alterado. |
| G5 | Inteligibilidade/naturalidade, loudness-alvo de plataforma e alinhamento legenda×fala real **não são medidos**. | `qualidade-piloto.md` | Só o humano confere (itens 2, 5, 6). |

## 2. Rastreabilidade de licenças por voz e asset

Fonte local = arquivo no disco deste PC lido nesta entrega. Nada foi baixado nem executado. Não é parecer jurídico.

| Item | Licença registrada no código | Fonte local verificada | Uso comercial |
|---|---|---|---|
| Pesos **Kokoro-82M** (pf_dora, pm_alex, pm_santa) | `Apache-2.0`, URL do card, nota dos riscos (`narration/kokoro.py`) | **[F]** `%LOCALAPPDATA%/Horus/tools/kokoro-proof/model/README.md`: metadado `license: apache-2.0`; seção de treino lista áudio **sintético de TTS fechados** e dados **CC BY 3.0/4.0** (Koniwa, SIWIS); `VOICES.md` com SHA e coluna CC BY. Não há arquivo `LICENSE` separado no diretório. | **Não verificado**: pesos Apache-2.0, mas direitos do áudio de treino não são afirmados; voz sai `commercial_use=unknown` |
| **espeak-ng** (fonemizador do Kokoro) | GPL-3.0 (nota), só no processo externo | [N] sem cópia local conferida nesta entrega (ver [avaliacao-vozes.md](avaliacao-vozes.md) §6.1) | n/a (não distribuído) |
| Voz **Microsoft Maria** (SAPI) | motor “Microsoft Windows” + nota “licença da voz deve ser verificada separadamente” | **[N]** nenhum arquivo local comprova termos da voz | **Não verificado**; `origin=system_voice`, `license=unverified` |
| **Piper** (cadu/faber/jeff) | GPL-3.0 do executável; “cada modelo possui licença própria” | **[F]** `relatorios/PILOTO-LOCAL-2026-11-13/escuta-piper/evidencias-reservadas/model-cards/{cadu,faber,jeff}/MODEL_CARD` (cadu: dataset CC0). Piper **não** é voz padrão do piloto | por modelo; não usado no primeiro vídeo |
| **ElevenLabs** | `Proprietary`, “plano não consultado; licença comercial unknown” | [N]; desligado, exige autorização de custo | `unknown` |
| **Fonte da legenda** Archivo Expanded SemiBold | OFL-1.1 declarada em `local_pilot.py`, `commercial_use=unknown`, não verificada | **[F]** `frontend/public/fonts/OFL-Archivo.txt` (SIL OFL 1.1) | Provável permissivo, mas fica `unverified` até decisão humana |
| Fonte alternativa Arial Black | “Microsoft Windows supplied font; licença não verificada” | [N] | `unknown` |
| **Cenas PNG** (geradas localmente do roteiro) | `original_local`, `license: original` no manifesto; na proveniência `commercial_use=unknown`, `status=unverified` | **[F]** geradas pelo próprio Hórus; autoria própria, mas sem decisão humana registrada | Pendente de verificação humana por asset |
| FFmpeg/Whisper (ferramentas, não entram no vídeo como asset) | — | **[F]** FFmpeg: `LICENSE.txt` GPL-3 (build gpl); faster-whisper-small: README `license: mit` | n/a |

### 2.1 Onde o bloqueio acontece (verificado)

- Todo asset de voz/narração/cena/fonte do piloto nasce `status=unverified`, `commercial_use=unknown` (`local_pilot.py`; `test_local_pilot.py`, `test_licensing_gates.py`). **Nenhum caminho automático grava `verified`**; só `POST /license-reviews` com `reviewed_by=human`, hash do asset, URL https e evidência.
- `unknown` não pode ser `verified` (contrato e API); decisão de hash antigo é ignorada (voz regenerada não herda aprovação).
- QC: asset não verificado → `asset_provenance` **warn** (não `fail`). Aprovação: **não bloqueada** por licença (G1). Publicação: **inexistente por desenho** (`publishable=false` sempre); metadados sempre trazem “Licenças pendentes” até todo asset ser `verified`+`allowed`.
- Portanto, “bloqueia o que não tiver licença” vale para **publicação e metadados**, não para a aprovação técnica do vídeo.

### 2.2 Testes negativos adicionados (`backend/tests/test_licensing_gates.py`, 29 passed + 1 xfail)

Motor sem licença/fonte/nota; voz padrão nunca comercial; matriz origem × estado (`unverified`, `rejected`, `verified`+`restricted`, sem humano) nunca permite uso comercial; uma voz não verificada mantém QC `warn` e divulgação pendente; `verified`+`restricted` permanece pendente; ausência de proveniência = pendente; aprovação não é herdada por arquivo de voz regenerado.

## 3. Como validar

```powershell
& backend/.venv/Scripts/python.exe -m pytest backend/tests/test_licensing_gates.py backend/tests/test_license_reviews.py backend/tests/test_license_consistency.py backend/tests/test_publishing.py backend/tests/test_publishing_adversarial.py -q
& backend/.venv/Scripts/python.exe -m pytest contracts/hermes/tests -q
```
