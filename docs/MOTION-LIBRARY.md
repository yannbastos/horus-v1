# Biblioteca Inteligente de Motion Design e Edição

Fonte canônica deste módulo **planejado**. Não há biblioteca inteligente operacional na implementação inspecionada; o **contrato do catálogo** (item abaixo) está implementado e testado, mas ainda sem catálogo real, API, busca na interface ou integração com o maestro. A [Biblioteca atual](../frontend/src/features/Library.tsx) registra/reproduz arquivos de mídia local; isso não implementa análise de edição, catálogo de técnicas ou recomendação.

## Objetivo

Transformar análises audiovisuais em uma biblioteca crescente de conhecimento e componentes práticos, reduzindo retrabalho e aumentando qualidade e consistência das produções.

Quando o usuário solicitar que a IA “desmonte” a edição de um vídeo, o sistema deverá estudar seus elementos audiovisuais, identificar técnicas relevantes e produzir registros estruturados reutilizáveis. Observações e parâmetros inferidos devem ser diferenciados do que foi efetivamente verificado.

## Registro de uma técnica — contrato implementado (HOR-058)

Cada técnica deverá conter:

- Nome, descrição, categoria e tags.
- Referência de origem e intervalo temporal correspondente.
- Parâmetros conhecidos, com limites e incertezas identificados.
- Quando disponíveis, demonstração visual e implementação reutilizável em Remotion.
- Evidência que permita distinguir análise, implementação e validação.

A referência de origem deve ser rastreável; analisar uma técnica não comprova licença para reutilizar imagens, áudio ou outros materiais do vídeo.

## Conhecimento, implementação e validação

| Situação conceitual | Significado |
|---|---|
| Técnica analisada | Registro descritivo fundamentado em uma referência; não implica código reutilizável |
| Modelo implementado | Existe componente/composição identificável; isso não comprova sua validação |
| Modelo validado | Há evidências de comportamento e resultado no contexto declarado |

Essas classificações são requisitos documentais, não enumerações de banco ou API existentes. Não anunciar modelo como validado apenas por haver código, demonstração ou um render isolado.

A fábrica já possui componentes Remotion como KenBurns e LegendaPalavra e a composição Short9x16. Sua existência não equivale ao catálogo inteligente e não constitui uma certificação de todos os usos. Os componentes permanecem na fábrica; esta reorganização não os altera.

## Uso e integração previstos

O usuário deverá poder pesquisar, filtrar, visualizar, organizar e selecionar modelos para novas produções. O maestro deverá consultar a biblioteca durante o planejamento e recomendar técnicas compatíveis com o objetivo editorial e a identidade visual de cada canal.

Registrar utilização e feedback do usuário, vinculados às versões pertinentes, para que a [memória operacional](AI-SYSTEM.md#fontes-oficiais-e-memória-ativa) contribua para recomendações futuras. Evitar duplicar o perfil editorial ou tratar preferência proposta como configuração aprovada.

Implementações reutilizáveis deverão usar o motor Remotion existente, respeitando as [fronteiras arquiteturais](ARCHITECTURE.md). Não criar um segundo motor de produção ou modificar a interface atual para antecipar o módulo.

## Evolução e limites

Integrar progressivamente após os marcos do [roadmap](ROADMAP.md), com escopo de implementação aprovado separadamente. Preservar a distinção entre biblioteca de mídia e biblioteca de técnicas; não excluir a capacidade atual nem renomear telas por iniciativa documental.

A biblioteca deve respeitar as [diretrizes de identidade visual](ARCHITECTURE.md#identidade-visual-e-equivalência), aprovações, licenças e orçamento. O [contexto original](historico/contexto-original/README.md) permanece como referência histórica integral.

## Contrato do catálogo de técnicas (HOR-058) — implementado, sem catálogo real

Código: `backend/src/horus_backend/motion_library.py` (puro: sem rede, disco, banco, subprocesso, download; **não ligado** à API, ao worker nem à fábrica). JSON Schema: `contracts/motion/technique-catalog.schema.json` (regenerar com `backend/.venv/Scripts/python.exe contracts/motion/generate_schemas.py`; teste de sincronia). Testes: `backend/tests/test_motion_library.py` (fontes fictícias). Mesmas convenções de `media_contracts.py`: modelos estritos (`extra=forbid`), `validate_technique_catalog` nunca levanta e devolve `{ok, errors, warnings, publishable:false, value}`.

Documento: `{schema_version:1, sources:[...], techniques:[...]}`.

- **Fonte verificável (`sources`):** `external_video` exige URL https sem credenciais, título e data da consulta; `local_media` exige o SHA-256 do arquivo e não declara URL; `verified_by`/`verified_at` (humano) são registrados à parte. `reuse.status` começa `unknown` e `allowed` exige verificação humana: analisar uma técnica não prova licença. O contrato **não busca** a URL (verificável = rastreável e conferível por uma pessoa).
- **Timecode de referência (`references`):** cada técnica tem ao menos uma `{source_id, start, end}` em segundos (≥ 0,04 s, dentro da duração declarada da fonte); `format_timecode`/`parse_timecode` convertem de/para `HH:MM:SS.mmm`.
- **Parâmetros:** `measured` (exige a referência de onde foi medido), `inferred` ou `estimated`; valores numéricos respeitam `min`/`max`.
- **Maturidade:** `analyzed` (sem implementação nem evidência), `implemented` (componente Remotion identificável da fábrica, apenas nome e caminho relativo; não é validação) e `validated` (contexto + evidência com SHA-256, incluindo ao menos uma de `test` ou `review`; um render isolado não basta). Com `artifacts` ({caminho: sha256}), o hash das evidências é cruzado com os artefatos registrados.
- **Catálogo:** ids e nomes únicos, `source_id` existente, sem campos extras (nenhum documento declara `publishable`). `search_techniques` filtra em memória (texto, categoria, tags, estado, fonte).

O catálogo real **começa vazio**: nenhuma técnica foi inventada nem extraída de vídeos. Fora deste incremento (planejado): persistência, API, interface de busca, análise assistida por IA, recomendação pelo maestro, registro de uso/feedback e novos componentes Remotion. Os componentes da fábrica não foram alterados.
