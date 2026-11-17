"""Safe refusal-path tests; never invoke a guest installer or a build."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parent

class GuestSafety(unittest.TestCase):
    def invoke(self, script, *args):
        # Let Windows PowerShell discover its own modules instead of inheriting
        # an incompatible PSModulePath from the IDE's PowerShell 7 process.
        env={k:v for k,v in os.environ.items() if k.upper() != 'PSMODULEPATH'}
        return subprocess.run(['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(ROOT / script), *map(str,args)], env=env, capture_output=True, text=True, timeout=30)

    def test_guest_requires_attestation(self):
        with tempfile.TemporaryDirectory() as t:
            out=Path(t)/'out'
            result=self.invoke('guest.ps1','-InputDirectory',t,'-OutputDirectory',out)
            self.assertNotEqual(result.returncode,0)
            self.assertIn('DisposableVm',result.stdout)
            self.assertFalse(out.exists())

    def test_guest_refuses_original_host(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t);(p/'manifest.json').write_text(json.dumps({'hostComputer':os.environ['COMPUTERNAME']}))
            out=p.parent/(p.name+'-evidence')
            result=self.invoke('guest.ps1','-InputDirectory',p,'-OutputDirectory',out,'-DisposableVm')
            self.assertNotEqual(result.returncode,0)
            self.assertIn('HOST REFUSED',result.stdout)
            self.assertFalse(out.exists())

    def test_input_output_overlap_refused(self):
        with tempfile.TemporaryDirectory() as t:
            result=self.invoke('guest.ps1','-InputDirectory',t,'-OutputDirectory',Path(t)/'out','-DisposableVm')
            self.assertNotEqual(result.returncode,0)
            self.assertIn('outside',result.stdout)
            self.assertFalse((Path(t)/'out').exists())

    def test_host_default_is_preflight_only(self):
        result=self.invoke('prepare-host.ps1')
        self.assertEqual(result.returncode,0,result.stderr)
        preflight=json.loads(result.stdout)
        self.assertFalse(preflight['buildRequested'])
        self.assertEqual(len(preflight['versions']),2)

    def test_host_refuses_nonincreasing_versions(self):
        result=self.invoke('prepare-host.ps1','-VersionN','1.2.3','-VersionNext','1.2.3')
        self.assertNotEqual(result.returncode,0)
        self.assertIn('newer',result.stderr)

    def test_sandbox_mappings(self):
        tree=ET.parse(ROOT/'rehearsal.example.wsb').getroot()
        self.assertEqual(tree.findtext('Networking'),'Disable')
        self.assertEqual([m.findtext('ReadOnly') for m in tree.findall('MappedFolders/MappedFolder')],['true','false'])
        self.assertIn('-DisposableVm',tree.findtext('LogonCommand/Command'))

    def test_mocked_installer_boundary(self):
        result=self.invoke('test-boundaries.ps1')
        self.assertEqual(result.returncode,0,result.stderr+result.stdout)
        self.assertIn('boundary assertions: 5',result.stdout)

if __name__=='__main__':
    unittest.main()
