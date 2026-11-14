# Cenas visuais locais — proposta para decisão

14/11/2026 · Leitura do Hórus `2782ecc` e da fábrica `5a5ad54`. **Não autoriza implementação.**
O irmão `fabrica-canais` foi localizado ao lado do checkout principal; não existe
`../fabrica-canais` ao lado deste worktree Orca.

## Capacidade comprovada por código

- **Fábrica:** uma composição, `Short9x16`, 1080×1920/30 fps; exportação H.264
  CRF 20/AAC 192k; rascunho em escala 0,5, CRF 30/AAC 128k. São presets de
  codificação, não estilos visuais. `KenBurns` aplica zoom/pan alternado à imagem
  local, com recorte `cover`; `LegendaPalavra` destaca a palavra ativa em grupos
  curtos. Há destaque tipográfico, mas não um sistema de títulos cinéticos.
- **Não encontrados no Remotion atual:** componentes de gráficos, ícones,
  transições editoriais ou B-roll. `Cena` aceita `imagem`, não um clipe com
  entrada/saída. O manifesto admitir `tipo=video` **não** significa que
  `Short9x16` o reproduza: usa `Img`, não um componente de vídeo.
- **Hórus:** gera PNGs originais por seção; o worker impõe
  `visual_override=scene-N.png`. O contrato atual fixa `visual_origin=original_local`
  e não recebe seleção de mídia. Com libass, esvazia `props.palavras` e embute o
  SRT posteriormente: o destaque nativo da fábrica não está ativo nesse caminho.
  Reativá-lo isoladamente duplicaria legendas. O contrato documental externo da
  fábrica é um rascunho, não uma API implementada.

## Assets disponíveis e limites de licença

**Disponíveis com termos locais:** `ArchivoExpanded-SemiBold.ttf` e
`GeistMono-Medium.ttf`, acompanhadas de SIL OFL em
[frontend/public/fonts](../frontend/public/fonts). Archivo já é opção das legendas;
Geist não está na whitelist de fonte desse caminho. O rasterizador dos cartões
ainda pede **Arial** ao Windows, e a legenda Remotion pede Arial Black: os termos
OFL não se transferem para essas fontes de sistema.

A busca incluindo arquivos ignorados, limitada a `fabrica-canais/canais`, não
encontrou PNG/JPG/WebP/SVG, MP4 ou WAV/MP3 presentes. Os dois manifestos históricos
consultados referenciam fotos Pixabay, com verificação de licença nula; um antigo
`publicavel=true` não é evidência atual de direitos nem de arquivo disponível.
Não foi identificado acervo pronto de fotos, ícones ou B-roll com licença
comprovada. Bibliotecas pessoais e temporários de ensaios não foram inventariados.
PNGs gerados como `original_local` também exigem autoria/termos e revisão humana;
essa etiqueta não constitui liberação comercial automática.

## Alternativas

Esforços abaixo são estimativas de implementação e validação por um programador,
não prazos medidos; pressupõem ferramentas já preparadas e nenhuma rede.

| Opção | Entrega e esforço | Risco e impacto no contrato | Decisão do usuário |
|---|---|---|---|
| **A — Ilustrações estáticas originais** | 1–2 dias: substituir blocos extensos por diagramas qualitativos, etapas e formas desenhadas localmente, com pouco texto. Continuar PNG + Ken Burns. | Baixo/médio: área segura, recorte pelo zoom e fonte. Sem alteração do contrato Hórus↔fábrica ou do Remotion; muda o gerador de imagens do Hórus. Não entrega animação independente por elemento. | Aprovar direção visual e 2–3 modelos; autorizar uso explícito da fonte OFL também nos PNGs. |
| **B — Motion nativo no Remotion** | 4–8 dias: componentes de títulos, ícones geométricos originais e diagramas animados sincronizados. | Médio/alto: componentes ainda precisam ser escritos; ampliar tipos/props e adaptação Python para cenas estruturadas, com fallback PNG e compatibilidade. Revisar hashes, sincronização e QC. | Autorizar mudanças na fábrica e no contrato; decidir linguagem visual e convivência com legendas libass. |
| **C — Mídia própria/licenciada local** | Fotos: 3–5 dias; clipes: 7–12. Importação confinada, seleção e evidência de licença por hash. | Fotos já cabem no renderer, mas o Hórus precisa deixar de impor PNG original e distinguir sua proveniência. B-roll requer também suporte novo de vídeo/trim/áudio na fábrica. Não há acervo pronto confirmado. | Fornecer/selecionar acervo com direitos comprovados e autorizar importador/contrato; para clipes, autorizar alteração da fábrica. |

## Recomendação e aceite

**A primeiro**, após aprovação visual: fluxo “observar → testar → revisar”,
comparação qualitativa e destaque de conceito. Usar geometria original e carregar
Archivo OFL privadamente no processo, sem instalar fontes globalmente. Não criar
gráficos factuais a partir de números soltos do roteiro; dados estruturados e
fontes precisam de aceite humano. Preservar narração integral, tempos, legendas,
licenças por hash, revisão e `publishable=false`.
Isso não resolve pendências da voz nem autoriza publicar.

Aceite proposto: comparar início/meio/fim de cada cena em rascunho e Final,
conferir zoom/área das legendas, texto sem recorte, proveniência das fontes e QC
sem falhas; aprovação visual humana antes de ampliar. A não resolve sozinha o
movimento interno: se isso for essencial, decidir B, mantendo o renderer único.
Nenhuma opção habilita Pexels/Pixabay ou permite reutilizar mídia sem direitos.

## Evidências e eficiência

Leitura dirigida, sem render, síntese, download de assets ou alteração na fábrica.
Fontes: Hórus [worker](../backend/src/horus_backend/production_worker.py),
[piloto](../backend/src/horus_backend/local_pilot.py),
[rasterizador](../backend/src/horus_backend/local_pilot_windows.py) e
[contrato](../backend/src/horus_backend/production_contract.py); na fábrica,
`scripts/fabrica/{render,modelos}.py`, `remotion/src/Root.tsx`, `remotion/src/tipos.ts`,
`composicoes/Short9x16.tsx` e `componentes/{KenBurns,LegendaPalavra}.tsx`.
Inspeção estática comprova existência e limites do código; não valida novos
visuais. Nenhum novo banco, catálogo ou motor é necessário para A.
