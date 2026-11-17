# HOR-159 — Whisper pelo gateway de saída

Data: 17/11/2026. Base: `60e14265cbfee77463175b993dac4cc92922bdfb`; branch `yannbastos/hor159-whisper`.

## Auditoria dos caminhos

Antes da implementação foram pesquisados os módulos do backend por `huggingface_hub`, `WhisperModel`, `download_model`, `hf_hub_download`, `snapshot_download`, `urllib`, `requests`, `httpx`, `socket`, `HF_*` e `HUGGINGFACE_*`; a conferência posterior abrangeu também os scripts do repositório. Linhas abaixo correspondem à entrega, salvo indicação de base.

| Caminho | Gateway? | Situação e fechamento |
|---|---|---|
| `backend/src/horus_backend/whisper_download.py:111` (`open_download`; base:70) | Antes não; agora sim | `authorize(whisper_model_download, GET, URL)` antes de abrir; HTTPS, ProxyHandler vazio, sem credenciais/cookies; streaming e integridade preservados. |
| `whisper_download.py:70` (`SafeRedirect`) e `:89` (`PinnedHTTPSHandler`) | Sim, a cada salto | Nova autorização auditada; `_check(resolve=True)` reutiliza validação de IP do gateway; conexão usa exclusivamente o IP retornado, TLS com SNI/hostname original; não há segunda resolução. |
| `transcriber.py:109` (`WhisperModel`, base:108) | Não precisa: offline | Caminho local resolvido, `local_files_only=True` existente; falta do modelo falha antes do carregador; `HF_HUB_OFFLINE`, `TRANSFORMERS_OFFLINE`, telemetria forçados antes dos imports, sem aceitar valor herdado 0. |
| `speech_alignment.py:545` (`WhisperModel`, base:544) | Não precisa: offline | Caminho local validado, `local_files_only=True`; offline agora também forçado na função chamada diretamente, antes dos imports. |
| `jobs.py:98–105` (ambiente do subprocesso de transcrição) | Não precisa: offline | Ambiente mínimo; três flags offline/telemetria forçadas após filtrar o ambiente; `HF_ENDPOINT` e credenciais HF não herdados; payload não admite parâmetros/env extras. |
| `speech_alignment.py:477–478,573` (subprocesso/main de alinhamento) | Não precisa: offline | Flags offline já forçadas após mesclar ambiente; mantidas. |
| `speech_alignment.py:427` (`resolve_model_path`) | Não precisa: disco | Busca diretórios e snapshots locais, inclusive refs locais; nenhuma chamada ao Hub. |
| `whisper_models.py:109,171` (lista/importação) e `whisper_download_catalog.py` | Não precisa: disco/metadados | Leitura/cópia/hash locais e catálogo versionado; URLs de licença não são abertas. |
| `transcriber.py:141` (`download`, yt-dlp) | Gate existente de `youtube_download` no job | Download de mídia YouTube, não de modelos; nenhuma alteração nesta finalidade. |
| `scripts/measure-transcription.py:148`, `smoke-windows-package.py:72`, `start-installed-local-pilot.ps1:47`, `test-installed-first-video.py:189` | Offline na transcrição | Wrappers já forçam offline e usam o backend acima; HTTP do smoke é apenas API loopback, não Hub. |
| `huggingface_hub`, `download_model`, `hf_hub_download`, `snapshot_download`, `HUGGINGFACE_*`, HTTP direto adicional nos módulos Whisper/alinhamento/modelos | Nenhum caminho adicional encontrado | A dependência faster-whisper poderia baixar por nome; ambos os pontos de entrada só recebem caminho local e `local_files_only=True`. |

## Política e privacidade

Regra explícita `whisper_model_download`, somente GET/HTTPS/443, sem auth nem loopback: `huggingface.co`, `cdn-lfs.huggingface.co`, `cdn-lfs-us-1.huggingface.co`, `cas-bridge.xethub.hf.co`. O primeiro é o destino direto do catálogo; os demais cobrem redirecionamentos de armazenamento LFS/Xet já permitidos antes desta tarefa. Não houve sondagem real para determinar qual CDN cada arquivo usaria hoje; preservou-se o conjunto existente, sem novos hosts nem wildcard. O transporte tem também limite fixo de hosts: uma regra extra de ambiente não o amplia. Nenhuma regra de Hermes/publicação foi ampliada.

Decisões permitidas e recusadas são gravadas em `transmissions`, estágio `egress:whisper_model_download`, com host/porta e códigos estáveis, sem query, caminho de arquivo, conteúdo ou credenciais. DNS privado é recusado e auditado antes do transporte. Download permanece em streaming (sem carregar pesos inteiros em memória), com confirmação humana, SHA-256, cancelamento, timeout e instalação atômica existentes.

## Verificação

- Focados de implementação: 114 aprovados (`test_whisper_egress`, `test_whisper_download`, `test_transcription_local_models`, `test_network_inventory`, `test_egress_gateway`), antes dos seis casos adicionais de paridade/ambiente/DNS privado.
- Coleta conjunta obrigatória: **2923 aprovados, 38 ignorados, 14 subtestes aprovados, 1 aviso**, em 746,94 s; exit code 0. O aviso é de serialização Pydantic em `test_production_contract.py::test_script_must_be_a_validated_model_and_is_revalidated` (valor textual `false` onde se espera bool), fora dos arquivos alterados.
- Interpretador existente: `C:/Users/yannn/01. Projetos/HORUS/horus/backend/.venv/Scripts/python.exe`; nenhuma instalação de dependência.
- `HORUS_DATA_DIR=%TEMP%/hor159-tests`, `PYTHONPATH=backend/src;contracts/hermes/src`; dados de testes em diretórios temporários; testes novos proíbem conexão real por socket e simulam DNS/HTTP/TLS/motores.
- Logs: `%TEMP%/hor159-focused.log`, `%TEMP%/hor159-full.log`.
- Sem app/perfil operacional, download real de modelo, frontend ou arquivos reservados de HOR-158; snapshot OpenAPI inalterado.
- `git diff --check`: aprovado.

Limite: não foi validado um download externo nem o carregamento de pesos reais/GPU nesta tarefa. Não houve push, merge ou PR.

Comandos de validação (com o interpretador e ambiente acima):

```powershell
python -m pytest backend/tests/test_whisper_egress.py backend/tests/test_whisper_download.py backend/tests/test_transcription_local_models.py backend/tests/test_network_inventory.py backend/tests/test_egress_gateway.py -q --tb=short
python -m pytest backend/tests contracts/hermes/tests -q --tb=short
```

A coleta conjunta inclui `test_whisper_models`, `test_egress_gateway`, `test_network_inventory`, `test_transmission_audit`, `test_supply_chain`, `test_endpoint_auth*`, bateria adversarial e os novos testes HOR-159; não se repete a suíte completa apenas para produzir contagens individuais.

## Entrega

Commit local: `2032302dee9ccd1026f71e9d2113821ba68e21b4` — `fix(whisper): submeter downloads ao gateway de saída`. Worktree limpa após o commit. Nova suíte HOR-159: 19 casos (incluindo parametrizações), cobertos pela coleta conjunta. Snapshot OpenAPI inalterado; sem falhas conhecidas desta alteração. Integração/revisão e publicação do commit ficam com o coordenador, conforme o despacho proíbe push/merge/PR.
