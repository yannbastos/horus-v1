# Atualização segura do banco v9 → v12 (HOR-157)

Este procedimento foi ensaiado somente com dados fictícios. **Não autoriza abrir, copiar ou migrar o banco operacional.** A decisão de executar no real pertence ao usuário/idealizador; o coordenador deve obter autorização separada e registrar a pasta e a versão escolhidas. Não iniciar Hórus, backend ou servidor de desenvolvimento enquanto essa decisão estiver pendente: a primeira inicialização do backend chama a migração automaticamente.

## Pré-condições e localização

Fechar app instalado, processos do backend e servidores de desenvolvimento; confirmar que nenhum processo continua escrevendo. Não apontar duas versões do aplicativo para a mesma pasta. Confirmar código aprovado com schema 12, Python/venv disponível e disco local com espaço para origem, exportação, restauração, cópia migrada, backup automático e rollback (reservar pelo menos seis vezes o tamanho dos dados incluídos, mais margem para WAL; mídia exige reserva adicional). TEMP é descartável: um backup real aprovado deve também ficar em armazenamento durável protegido e não sincronizado, antes de qualquer atualização.

Os caminhos reais são descritos, **não acessados pelo ensaio**: o desktop Tauri utiliza a pasta de dados de `com.horus.audiovisual` sob `%LOCALAPPDATA%`; o backend independente usa por padrão `%LOCALAPPDATA%\Horus\workspace`. `HORUS_DATA_DIR` pode substituir a origem. A pasta `%LOCALAPPDATA%\Horus` também pode conter dados legados/modelos. Confirmar a configuração efetiva sem adivinhar a origem. Esta entrega não inspecionou nenhum desses diretórios.

## Ensaio reproduzível autorizado

Na raiz desta worktree, usando o venv existente do checkout original (substitua apenas `$python` se possuir outro venv equivalente):

```powershell
$python = 'C:\Users\yannn\01. Projetos\HORUS\horus\backend\.venv\Scripts\python.exe'
$env:PYTHONPATH = "$PWD\backend\src"
$env:PYTHONIOENCODING = 'utf-8'
$env:HORUS_DATA_DIR = "$env:TEMP\hor157-isolamento"
$ensaio = Join-Path $env:TEMP ('hor157-ensaio-' + [guid]::NewGuid().ToString('N'))
& $python scripts/ensaio_atualizacao_banco.py --destino $ensaio
if ($LASTEXITCODE -ne 0) { throw 'NO-GO: ensaio falhou' }
```

O script exige destino novo sob TEMP, resolve links no caminho e recusa ambos os diretórios operacionais descritos acima, inclusive descendentes. Não aceita origem externa: ele próprio constrói o banco v9 aplicando somente migrações 1–9 e insere mídia, workflow, job, tentativa, artefato, roteiro humano e recibo fictícios. Cada etapa imprime PASSOU; qualquer exceção imprime FALHOU e retorna 1. Mantém arquivos e JSON de hashes como evidência; não apaga backups.

## Comandos de inspeção e recuperação (todos sobre o ensaio)

Após o ensaio, `$ensaio` contém `origem`, `restaurado`, `migrado`, `pacote.zip`, `rollback.sqlite3` e hashes. Para executar as operações individualmente em destinos novos, use o seguinte bloco; os comandos de exportação, verificação e restauração são os CLIs reais, também executados pelo script:

```powershell
$origem = Join-Path $ensaio 'origem'
$pacote = Join-Path $ensaio 'pacote-individual.zip'
$restaurado = Join-Path $ensaio 'restaurado-individual'
& $python -m horus_backend.local_data_export export $origem --to $pacote
if ($LASTEXITCODE -ne 0) { throw 'NO-GO: exportação' }
& $python -m horus_backend.local_data_export verify $pacote
if ($LASTEXITCODE -ne 0) { throw 'NO-GO: pacote inválido' }
& $python -m horus_backend.local_data_export restore $pacote --to $restaurado
if ($LASTEXITCODE -ne 0) { throw 'NO-GO: restauração' }
& $python -m horus_backend.backups verify "$restaurado\horus.sqlite3" --json
if ($LASTEXITCODE -ne 0) { throw 'NO-GO: integridade' }
& $python -c 'import sqlite3,sys; from contextlib import closing; from pathlib import Path; p=Path(sys.argv[1]); assert p.is_file(); db=sqlite3.connect(p.resolve().as_uri()+"?mode=ro",uri=True); print(db.execute("PRAGMA integrity_check").fetchall()); print(db.execute("PRAGMA user_version").fetchone()); db.close()' "$restaurado\horus.sqlite3"
```

`verify` deve retornar `ok: true`, integridade `ok` e versão 9. O verificador recusa WAL/journal pendente: não excluir sidecars para contornar a recusa. Fechar o escritor e resolver o checkpoint na cópia sob supervisão antes de prosseguir. A API SQLite de backup usada na exportação fornece uma cópia consistente; cópia simples de um arquivo aberto não substitui isso.

A exportação possui SHA-256 por arquivo no manifesto e confere integridade dos SQLite. Ela **não é backup completo da instalação**: exclui mídia, modelos, exports finais e credenciais/integrações Hermes. Salvar esses itens separadamente requer escopo e autorização; depois de restaurar, credenciais precisam ser reconfiguradas. Não prometer recuperar mídia a partir do ZIP.

### Contagens e hashes lógicos

Hashes do arquivo SQLite podem mudar com checkpoint/backup/migração sem perda de dados. Comparar o conteúdo lógico de todas as tabelas com o utilitário `snapshot` do ensaio (inclui contagens, colunas, ordenação determinística de linhas, SHA-256 e `foreign_key_check`). Para comparar origem e restauração:

```powershell
& $python -c 'import sys; from pathlib import Path; sys.path.insert(0,"scripts"); from ensaio_atualizacao_banco import snapshot; a=snapshot(Path(sys.argv[1])); b=snapshot(Path(sys.argv[2])); assert a==b; print("PASSOU hashes e contagens")' "$origem\horus.sqlite3" "$restaurado\horus.sqlite3"
```

### Migrar uma cópia, verificar backup e reabrir

Nunca executar o comando de migração abaixo apontando para a origem. A restauração anterior ainda está v9; criar outro arquivo exclusivo, migrá-lo e conferir a versão:

```powershell
$copia = Join-Path $ensaio 'copia-individual.sqlite3'
& $python -m horus_backend.backups restore "$restaurado\horus.sqlite3" --to $copia --json
if ($LASTEXITCODE -ne 0) { throw 'NO-GO: cópia' }
& $python -c 'import sys; from pathlib import Path; from horus_backend.migrations import migrate; migrate(Path(sys.argv[1]))' $copia
if ($LASTEXITCODE -ne 0) { throw 'NO-GO: migração' }
& $python -m horus_backend.backups verify $copia --json
if ($LASTEXITCODE -ne 0) { throw 'NO-GO: backup ou integridade' }
```

A migração cria `<stem>.backup-v9-<uuid>.sqlite3` antes de alterar o schema; no banco padrão, `horus.backup-v9-*.sqlite3`. Exigir backup íntegro v9 e conteúdo igual ao snapshot original. A v10 adiciona `scripts.generator` (NULL nos dados anteriores), v11 cria `transmissions` e v12 cria `human_checkpoints`. A comparação pós-migração projeta as colunas v9: todas as linhas anteriores devem permanecer iguais. As novas tabelas começam vazias. O ensaio verifica `PRAGMA user_version = 12`, integridade, hashes antigos e idempotência chamando `migrate` novamente, comparando todo o conteúdo v12 e confirmando que não surgiu outro backup.

### Rollback

Antes de qualquer execução real autorizada, preservar o banco atual e sidecars juntos numa pasta de segurança com o app fechado. Não sobrescrever nem misturar WAL/SHM antigos com o restaurado. Restaurar primeiro para arquivo/pasta nova e verificar; apenas com autorização separada trocar a pasta de dados configurada. Para manter v9, não iniciar backend v12: ele migrará novamente. Usar uma versão compatível aprovada ou manter o banco restaurado fechado até a decisão do usuário.

No ensaio, ambos os caminhos foram testados: restauração do pacote e restauração do backup automático com `python -m horus_backend.backups restore <backup> --to <arquivo-novo> --json`. O script compara o rollback com todas as contagens e hashes v9 e garante que a origem permaneceu igual. Destinos existentes são recusados pelas ferramentas.

## Go/no-go e registro

- GO técnico somente com app/backend/dev fechados, espaço e origem confirmados, versão 9 conhecida, exportação verificada, restauração conferida, integridade e hashes preservados, cópia migrada v12, backup v9 íntegro, reabertura idempotente e rollback ensaiado.
- NO-GO para qualquer falha, versão inesperada, WAL/journal pendente, backup ausente, diferença de dados não explicada, mídia necessária sem cópia independente ou falta de autorização. Preservar evidências e pedir decisão; não corrigir o banco operacional por tentativa.
- Registrar data, operador, autorização específica, versão do app/commit, caminho escolhido (em registro privado), tamanho/espaço, versão SQLite inicial/final, comandos e códigos de saída, manifesto/SHA-256 do pacote, contagens/hashes por tabela, nome e verificação do backup, resultado da restauração e rollback. Guardar logs sem credenciais e não publicar conteúdo do banco.
- Aprovação dos testes sintéticos não valida dados reais, instalação, espaço real disponível, encerramento dos processos reais nem compatibilidade de conteúdo operacional. Tudo isso permanece pendente de uma execução supervisionada expressamente autorizada pelo usuário.

## Condições bloqueantes adicionais (revisão independente)

**NO-GO sem cópia física fria integral verificada**, mesmo com ZIP válido. Somente após autorização real separada, fechar todos os processos e copiar a pasta de dados inteira para armazenamento seguro fora dela, incluindo mídia, modelos, configurações, credenciais e todos os SQLite e sidecars. Proteger o acesso ao destino por conter segredos. Não executar essa cópia nesta entrega. Conferir entre origem e destino contagem de arquivos, soma dos tamanhos e SHA-256 de cada SQLite, `-wal` e `-shm`; registrar também sidecars ausentes, data, caminhos e resultados em registro privado durável. Qualquer diferença bloqueia a atualização. O ZIP cobre apenas os bancos Hórus/Radar e configurações JSON permitidas pelo exportador; não substitui a cópia integral, pois exclui mídia, modelos, exports e credenciais.

Comandos de inspeção, para uso supervisionado autorizado (não encerram processos):

```powershell
Get-Process -Name 'horus*','python*','node*' -ErrorAction SilentlyContinue | Select-Object Id,ProcessName,Path
Get-PSDrive -PSProvider FileSystem | Select-Object Name,Used,Free,Root
```

A lista de processos é triagem, não prova ausência de handles. No Monitor de Recursos (`resmon.exe`), CPU → Identificadores Associados, buscar o nome e caminho do SQLite; qualquer detentor bloqueia a cópia. Reservar espaço também para a cópia integral, além das seis cópias dos dados exportáveis já previstas.

### WAL em cópia exclusiva

Preservar a cópia fria intacta. Criar uma segunda cópia descartável do SQLite **junto com seus sidecars de mesmo nome**, todos obtidos com escritores fechados. Nunca apagar WAL manualmente nem executar checkpoint no original. Inspecionar os arquivos `*.sqlite3-wal`, `*.sqlite3-shm` e `*.sqlite3-journal`: sidecar sem o SQLite correspondente é órfão; ausência de WAL junto de SHM também exige investigação. Não juntar arquivos de momentos diferentes, não migrar e não descartar órfãos para liberar o procedimento.

Com o executável `sqlite3` já disponível, somente sobre essa segunda cópia:

```powershell
sqlite3 $copia 'PRAGMA wal_checkpoint(TRUNCATE); PRAGMA integrity_check;'
if ($LASTEXITCODE -ne 0) { throw 'NO-GO: checkpoint' }
```

Exigir retorno do checkpoint com `busy=0` (em WAL truncado, `0|0|0`) e integridade `ok`; depois fechar a conexão e repetir `backups verify` sobre a cópia. Se não houver ferramenta, houver busy ou journal pendente, parar e pedir análise; não improvisar no original.

### Hash externo do ZIP

Imediatamente após exportar, registrar em local protegido o SHA-256 externo; repetir antes e depois da restauração/transporte e comparar ao registro original:

```powershell
$hashOriginal = (Get-FileHash -LiteralPath $pacote -Algorithm SHA256).Hash
# Guardar hashOriginal em registro durável separado antes de transportar.
if ((Get-FileHash -LiteralPath $pacote -Algorithm SHA256).Hash -ne $hashOriginal) { throw 'NO-GO: ZIP alterado' }
# Repetir a comparação depois do restore; o manifesto interno continua obrigatório.
```

### Contraindicações e efeitos do rollback

Não usar rollback depois de qualquer gravação na v12 sem antes preservar integralmente o estado v12 e decidir como recuperar os dados novos. Restaurar v9 perde, no destino restaurado, todas as gravações posteriores ao backup, inclusive transmissões, checkpoints humanos e alterações de roteiros. Não existe downgrade automático que mescle esses dados. Nunca restaurar sobre o único exemplar atual.

Rollback somente a partir da cópia física fria verificada ou de `horus.backup-v9-*` verificado. A cópia integral recupera o estado de toda a pasta no instante registrado; o backup automático recupera apenas o SQLite Hórus naquele instante, sem recuperar mídia, Radar, configurações ou credenciais. O ZIP pode servir à recuperação parcial ensaiada, mas não é fonte suficiente de rollback integral. Preservar as duas versões e resolver qualquer divergência antes de retomar a operação.

### Cobertura e limites do ensaio

O snapshot compara todas as tabelas e colunas v9, mesmo vazias. Há linhas em `media`, `workflows`, `jobs`, `job_attempts`, `artifacts`, `scripts` e `script_receipts`; as demais tabelas v9 não têm linhas, portanto sua preservação de conteúdo não foi exercitada. As novas tabelas `transmissions` e `human_checkpoints` são verificadas vazias, e `scripts.generator` é verificado NULL. O ensaio inclui agora um banco Radar sintético com snapshot na restauração e compara o hash externo do ZIP. Não cobre dados reais, volume, concorrência, recuperação de WAL pendente, mídia, modelos, credenciais ou todas as restrições de domínio das tabelas vazias.

Testes da guarda (sem dados reais): `python -m unittest discover -s scripts -p test_ensaio_atualizacao_banco.py -v`. Simulam perfil e variáveis em TEMP, recusando relativos, escape, raiz TEMP, pastas operacionais com caixa diferente, TEMP adulterado e junction/link.
