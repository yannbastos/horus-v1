# ADR — persistência local e remota (HOR-137, Fase A)

**Atualização da Fase B (15/11/2026):** [hospedagem, schema e provas](persistencia-fase-b.md) registram a escolha de Edge Functions para o serviço futuro e o schema sintético aplicado no horus-dev, com RLS validado e fixtures revertidas. Os trechos abaixo descrevem a decisão da Fase A; o serviço remoto, a sessão e a sincronização do app continuam sem implementação operacional.

**Estado: aceita pelo Maestro em 15/11/2026 como direção das Fases B–D** (SQLite local continua fonte da operação audiovisual; Supabase como destino opcional de metadados sincronizáveis por adesão; acesso pela opção B — serviço remoto autenticado com JWT individual e papel restrito, Data API desativada). A hospedagem do serviço B (ex.: Edge Functions do próprio horus-dev, dentro do Free) é decidida na Fase B. **Fase E (dados reais) continua exigindo aprovação específica do usuário.** Base inspecionada: `399f305` da `main`; [inventário e contrato proposto](inventario-persistencia.md). Mantêm-se os requisitos [R1–R13 de segurança](seguranca-persistencia-remota.md) e a [especificação de UX](ux-sincronizacao.md), cuja integração é dependência documental. Nenhum dado operacional foi aberto ou enviado à nuvem.

## Recomendação

Manter SQLite como fonte da operação audiovisual neste computador. Adotar PostgreSQL/Supabase somente como destino opcional de metadados selecionados e histórico sincronizável, depois das validações das fases seguintes. Não substituir `Store` por uma conexão remota genérica: jobs, checkpoints, subprocessos e arquivos precisam de transações locais mesmo sem conta, internet ou serviço disponível. Não duplicar executor, fábrica/CLI/Remotion ou memória Hermes.

Com a Data API **desativada**, recomendar a opção **B** da análise de segurança: serviço remoto autenticado, JWT individual validado no servidor e acesso Postgres por papel restrito, sem `BYPASSRLS`, com escopo de usuário transacional e RLS. Segredo de banco existe somente nesse ambiente confiável; o FastAPI local continua cliente público. Hospedagem/deploy desse componente ainda precisa de decisão e autorização; não está disponível hoje. Se isso não for aprovado, permanecer integralmente local. Não habilitar Data API, exposição automática, add-ons ou cobrança. A alternativa C (Data API restrita + RLS) só pode ser reconsiderada com autorização explícita do usuário; conexão privilegiada no desktop e proxy local com senha são rejeitados por R1–R4.

## Separação proposta por domínio

| Domínio | Destino e autoridade | Motivo e consequência offline |
|---|---|---|
| Jobs, eventos técnicos, tentativas, checkpoints de transcrição/download, recibos de submissão e locks | Local | Retomada/cancelamento e execução pertencem à máquina; não replicar fila nem reexecutar pelo recebimento remoto |
| Mídia, caminhos, modelos, áudio/vídeo, renders, temporários, imports e pacotes de exportação/backup | Local | Volume, privacidade e dependência física; remoto poderá receber apenas manifesto permitido e hashes, sem caminho absoluto; mídia ausente em outro PC deve ser explícita |
| Workflows editoriais, roteiros e versões, artefatos textuais, comentários, revisões de produção/licença | Sincronizável por adesão e seleção explícitas | Cópia local de trabalho e operação offline preservadas; remoto é autoridade da revisão compartilhada confirmada, sem invalidar cópias locais ainda pendentes |
| Aprovações/autorização de gasto e publicação | Local e vinculadas a conteúdo/revisão/operador; histórico elegível a sincronização | Histórico remoto nunca vira permissão executável em outro PC; nenhuma ação de gasto/publicação nasce de sync |
| Radar: cache, janela de cota, bloqueios, lock e datasets expirantes | Local | Cota local não é controle financeiro global; manter expiração/redação também em cópias futuras. Não enviar payload bruto ao remoto por padrão |
| Notificações lidas, preferências de narração/UI, recibo WebView e diagnósticos | Local | Derivados ou específicos da máquina; não justificam conflitos remotos agora |
| Identidade individual, associação explícita a espaços compartilhados, revisão/recibos de sincronização e cursor remoto | Remoto (novos conceitos, não implementados) | RLS/servidor são autoridade; sessão no cofre do SO; offline usa somente o workspace local permitido, sem prometer acesso a itens nunca baixados |
| Hermes/configurações críticas/credenciais e dados próprios da fábrica | Fora da transferência | Sem copiar segredos, alterar fronteiras ou integrar `integ-hermes-line-v3` |

## Contrato e consequências

Repositórios por domínio e unidade de trabalho local preservam os contratos atuais da API e a atomicidade de aprovação + revisão + evento. Publicação de mudanças usa outbox transacional e recibo remoto idempotente; leitura remota entra em inbox e não em JobManager. Concorrência usa versão do servidor e conflito explícito, sem ordenar por relógio do cliente. Detalhes, etapas de transferência, recuperação, medições e custos estão no anexo. Nada disso foi implementado.

O SQLite remanescente **não será apenas cache descartável**: texto de transcrição, roteiros offline, comentários e outbox podem conter conteúdo privado não sincronizado. Por isso, a sugestão de adiar SQLCipher na análise de segurança é condicional; não encerrar HOR-121 sem medir conteúdo/retensão e avaliar criptografia de volume. Logout remove sessão e cache exclusivamente remoto (R7), mas não pode apagar trabalho local ou pendente; a futura separação física/lógica deve permitir ambas as garantias.

O plano Free é adequado ao ensaio sintético, não uma garantia de disponibilidade/backup para produção. Pausa, cotas e indisponibilidade devem somente suspender a sincronização. Principais riscos: perda de pendências por limpeza indevida; falsa aprovação transportada; divergência entre JSON e banco; índices/RLS incompletos; aumento de volume por históricos; exaustão de disco/WAL. Cada risco tem critérios de validação no anexo e em R1–R13.

## Portas das próximas fases

- **B — contratos e prova isolada:** decidir acesso B, identidade e retenção; especificar schema remoto/repositórios com dados sintéticos; testes de RLS com duas contas, sem implantar por este ADR.
- **C — transferência sintética:** snapshot, mapa de IDs, ordem de dependências, reenvio idempotente, validação de hashes e ensaio de restauração.
- **D — sincronização/UX:** offline, conflitos, sessão expirada, crash antes/depois do commit e regressão do primeiro vídeo; cumprir R1–R13.
- **E — dados reais:** exige aprovação específica, plano revisado de backup/retorno e restauração já comprovada; escopo e volume explícitos. Não remover SQLite nem autorizar nuvem como dependência da produção local.

O Maestro deve registrar B–E como Issues próprias após a decisão, manter HOR-137 aberta até os aceites completos e reconciliar UX/segurança. Este ADR não concede autorização estrutural permanente.
