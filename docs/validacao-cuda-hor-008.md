# CUDA e fallback — HOR-008

A transcrição usa `HORUS_WHISPER_DEVICE=auto|cuda|cpu` (padrão `auto`), CUDA/float16 ou CPU/int8. O alinhamento continua CPU/int8 por padrão; seu subprocesso aceita a mesma variável quando fornecida no ambiente do chamador. O ambiente restrito da produção não passa essa variável automaticamente. Modelo local obrigatório: `models/<nome>`, `models/faster-whisper-<nome>` ou essas pastas dentro de `models/whisper`; caches Hub anteriores com `refs/main` e snapshot existente também são reconhecidos. Nenhum download de modelo ocorre em runtime.

GPU detectada não comprova DLLs utilizáveis: a carga pode funcionar e a primeira inferência falhar em cuBLAS/cuDNN. Nesse caso, ou em falta de VRAM, há uma única tentativa CPU antes do primeiro texto/palavra. Após texto emitido, a transcrição falha preservando o parcial, sem repetição automática; o alinhamento só publica JSON completo e falha sem publicar palavras incompletas. Falhas nativas sem exceção Python continuam dependentes de timeout/cancelamento do subprocesso.

## Reprodução offline

Use um WAV sintético com roteiro conhecido e um modelo CTranslate2 já instalado. Configure `HORUS_TEST_CUDA=1`, `HORUS_TEST_MODEL_PATH` (pasta contendo `model.bin`), `HORUS_TEST_WAV` e `HORUS_TEST_SCRIPT` (texto exato). Execute `python -m pytest backend/tests/test_cuda_validation.py -q -s`; sem a flag, os três casos de hardware são ignorados. Cada caso usa subprocesso separado e timeout de 180 s; os dados de teste ficam em diretório temporário. Para executar só CPU, acrescente `-k cpu-int8`.

As bibliotecas CUDA/cuDNN compatíveis com o CTranslate2 devem estar provisionadas no PATH do processo; o app não instala drivers/DLLs. O teste cobre CUDA float16, CUDA int8 e CPU int8, imprime tempo de carga + inferência + alinhamento, e mede VRAM total da GPU via `nvidia-smi` a cada aproximadamente 100 ms (inclui outros processos, não representa alocação exclusiva do modelo). Reserve a GPU antes de comparar. Os modos adicionais `alignment`/`transcription` exercitam o código do aplicativo em processos independentes.

## Evidência de 14/11/2026

RTX 3050 Laptop, 6 GiB, driver 621.24, CTranslate2 4.9.0, modelo `small` previamente instalado, WAV SAPI pt-BR sintético de 19,30 s. O ambiente inicial não continha `cublas64_12.dll`: falha real de inferência e fallback CPU/int8 confirmado, com 31 palavras reconhecidas. Driver ausente, VRAM insuficiente e falha após parcial são injetados por mocks, sem remover driver nem esgotar a GPU do operador. Bibliotecas NVIDIA de teste (cuBLAS 12.9.2.10, cuDNN 9.28.1.4, NVRTC 12.9.86) foram provisionadas somente em pasta temporária, sem alterar o app ou os dados operacionais.

| Caminho, modelo `small` | Tempo (s) | VRAM inicial/pico total (MiB) |
|---|---:|---:|
| ASR + alinhamento, CUDA float16 | 1,409 | 230 / 984 |
| ASR + alinhamento, CUDA int8 | 1,626 | 230 / 696 |
| ASR + alinhamento, CPU int8 | 4,496 | 221 / 221 |
| Alinhamento do app, CUDA float16 | 1,378 | 240 / 984 |
| Transcrição do app, CUDA float16 | 1,325 | 236 / 1016 |
| Alinhamento do app, CPU int8 | 4,976 | 221 / 221 |
| Transcrição do app, CPU int8 | 4,409 | 221 / 221 |
| Alinhamento com cuBLAS ausente → CPU int8 | 9,031 | 230 / 774 |
| Transcrição com cuBLAS ausente → CPU int8 | 11,282 | 230 / 870 |

Os três casos de hardware passaram (15 testes no arquivo completo, cada caminho isolado; GPU reservada pelo coordenador), com alinhamento acima dos critérios existentes de casamento/tempo. `int8` seleciona efetivamente `int8_float16` na GPU e `int8_float32` na CPU no CTranslate2 desta máquina. A regressão focada final, incluindo o snapshot da API, passou com 156 testes aprovados e quatro opt-in ignorados. Medições únicas, com cache de disco aquecido, não constituem benchmark estatístico nem homologam modelos maiores, áudio longo ou instalador. Reabrir modelos repetidamente no mesmo processo travou no ensaio inicial; o app e o teste usam subprocessos independentes, e o teste aplica timeout externo para detectar travamentos. O instalador permanece dependente de provisionamento explícito das DLLs; a detecção de GPU não certifica essas bibliotecas.

Para repetir a falha de bibliotecas sem alterar o driver, retire apenas as pastas CUDA temporárias do PATH do processo de teste e use `HORUS_TEST_CUDA_CHILD=1`, `HORUS_TEST_CUDA_MODE=alignment` (ou `transcription`), `HORUS_TEST_EXPECT_FALLBACK=1`, executando o nó `test_real_cuda_offline[cuda-float16]`. Esse modo avançado pula o invólucro de subprocesso do teste: execute-o em processo descartável com limite externo. Não remova DLLs instaladas nem modifique o driver do operador.
