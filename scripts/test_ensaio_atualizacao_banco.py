"""Recusas sem acessar dados operacionais; perfil simulado inteiramente em TEMP."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from ensaio_atualizacao_banco import safe_path

class GuardTests(unittest.TestCase):
    def test_recusas(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            profile = root / 'perfil'
            local = profile / 'AppData' / 'Local'
            temp = root / 'temp'
            temp.mkdir()
            with patch.object(Path, 'home', return_value=profile), patch.dict(os.environ, {'TEMP': str(temp), 'LOCALAPPDATA': str(root / 'falso')}):
                for path in (Path('relativo'), temp, root / 'fora', temp / '..' / 'fora', local / 'Horus' / 'x', local / 'COM.HORUS.AUDIOVISUAL' / 'x'):
                    with self.subTest(path=path), self.assertRaises(ValueError):
                        safe_path(path)
                self.assertEqual(safe_path(temp / 'exclusivo'), temp / 'exclusivo')
                with patch.dict(os.environ, {'TEMP': str(local)}):
                    with self.assertRaises(ValueError):
                        safe_path(local / 'hOrUs' / 'x')
                with patch.dict(os.environ, {'TEMP': 'relativo'}):
                    with self.assertRaises(ValueError):
                        safe_path(temp / 'x')
                link = temp / 'junction'
                if os.name == 'nt':
                    subprocess.run(['cmd', '/c', 'mklink', '/J', str(link), str(temp)], check=True, capture_output=True)
                else:
                    link.symlink_to(temp, target_is_directory=True)
                try:
                    with self.assertRaises(ValueError):
                        safe_path(link / 'x')
                finally:
                    if os.name == 'nt':
                        link.rmdir()
                    else:
                        link.unlink()

if __name__ == '__main__':
    unittest.main()
