"""Entrada manual opcional; rede somente após autorização e --allow-network. Usa PostgreSQL via I1."""
import argparse
import os
from pathlib import Path
import sys

from ..database.manager import DatabaseManager, DatabaseRefused
from ..database.vault import ConnectionVault
from . import CollectionRequest, GoogleTransport, RadarState, YouTubeCollector


def main(argv=None, *, db=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--request', type=Path, required=True, help='JSON conforme CollectionRequest')
    parser.add_argument('--state-dir', type=Path, required=True, help='Diretório de dados associado ao cofre PostgreSQL')
    parser.add_argument('--allow-network', action='store_true')
    args = parser.parse_args(argv)
    if not args.allow_network:
        parser.error('Rede desabilitada. Esta entrega é validada apenas com mocks; coleta real exige autorização.')
    key = os.environ.get('YOUTUBE_API_KEY', '')
    if not key:
        parser.error('Configure YOUTUBE_API_KEY fora do código/Git; não informe chave na linha de comando.')
    request = CollectionRequest.model_validate_json(args.request.read_text(encoding='utf-8'))

    owns_db = db is None
    manager = db
    if owns_db:
        manager = DatabaseManager(ConnectionVault(args.state_dir))
        status = manager.test()
        if status.state != 'connected':
            code = status.last_error_code or 'database_unavailable'
            manager.close()
            sys.stderr.write(f'Erro de conexão PostgreSQL ({code}; estado={status.state}).\n')
            raise SystemExit(3)

    try:
        state = RadarState(manager)
        dataset = YouTubeCollector(GoogleTransport(key, allow_network=True), state).collect(request)
        print(dataset.model_dump_json(indent=2))
    except DatabaseRefused as error:
        sys.stderr.write(f'Erro de conexão PostgreSQL ({error.code}).\n')
        raise SystemExit(3) from None
    finally:
        if owns_db and manager is not None:
            manager.close()


if __name__ == '__main__':
    main()
