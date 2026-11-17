"""Run the actual NSIS location function against a private fixture registry key."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import uuid
import winreg

REPO = Path(__file__).resolve().parents[1]
TEMPLATE = REPO / 'frontend/src-tauri/installer.nsi'
NSIS = Path(os.environ['LOCALAPPDATA']) / 'tauri/NSIS/makensis.exe'

@unittest.skipUnless(os.name == 'nt' and NSIS.exists(), 'Windows and local NSIS required')
class LocationPolicy(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sandbox = tempfile.TemporaryDirectory(prefix='hor147-policy-')
        cls.root = Path(cls.sandbox.name)
        cls.key = 'Software\\HorusInstallerPolicyTest-' + uuid.uuid4().hex
        source = TEMPLATE.read_text(encoding='utf-8')
        function = source[source.index('Function RestorePreviousInstallLocation\n'):]
        function = function[:function.index('FunctionEnd') + len('FunctionEnd')]
        cls.default = str(Path(os.environ['LOCALAPPDATA']) / 'Programs/Horus')
        script = r'''
Unicode true
RequestExecutionLevel user
!include LogicLib.nsh
!include FileFunc.nsh
!define MANUPRODUCTKEY "@KEY@"
!define UNINSTKEY "@KEY@\Uninstall"
Name "HORUS Policy Fixture"
OutFile "@ROOT@\policy.exe"
InstallDir "placeholder"
SilentInstall silent
Function .onInit
  SetShellVarContext current
  SetRegView 64
  ${If} $INSTDIR == "placeholder"
    StrCpy $INSTDIR "$LOCALAPPDATA\Programs\Horus"
    Call RestorePreviousInstallLocation
  ${EndIf}
FunctionEnd
@FUNCTION@
Section
  FileOpen $0 "@ROOT@\result.txt" w
  FileWrite $0 $INSTDIR
  FileClose $0
SectionEnd
'''.replace('@KEY@', cls.key).replace('@ROOT@', str(cls.root)).replace('@FUNCTION@', function)
        old_function = '''Function RestorePreviousInstallLocation
  ReadRegStr $4 SHCTX "${MANUPRODUCTKEY}" ""
  StrCmp $4 "" +2 0
    StrCpy $INSTDIR $4
FunctionEnd'''
        old_script = script.replace(function, old_function).replace('policy.exe', 'old-policy.exe')
        (cls.root / 'old.nsi').write_text(old_script, encoding='utf-8')
        subprocess.run([str(NSIS), '/V2', str(cls.root / 'old.nsi')], check=True, capture_output=True)
        (cls.root / 'policy.nsi').write_text(script, encoding='utf-8')
        subprocess.run([str(NSIS), '/V2', str(cls.root / 'policy.nsi')], check=True, capture_output=True)

    def tearDown(self):
        for key in (self.key + '\\Uninstall', self.key):
            try:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key)
            except FileNotFoundError:
                pass

    @classmethod
    def tearDownClass(cls):
        cls.sandbox.cleanup()

    def run_policy(self, remembered=None, active=False, explicit=None, expected_code=0, executable='policy.exe'):
        if remembered is not None:
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, self.key) as key:
                winreg.SetValueEx(key, '', 0, winreg.REG_SZ, remembered)
        if active:
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, self.key + '\\Uninstall') as key:
                winreg.SetValueEx(key, 'UninstallString', 0, winreg.REG_SZ, 'fixture-uninstall.exe')
        result = self.root / 'result.txt'
        result.unlink(missing_ok=True)
        command = subprocess.list2cmdline([str(self.root / executable)])
        if explicit:
            command += ' /D=' + explicit
        process = subprocess.run(command, timeout=15)
        self.assertEqual(process.returncode, expected_code)
        if expected_code:
            self.assertFalse(result.exists())
            return None
        return result.read_text(encoding='mbcs')

    def test_original_template_reproduces_temp_inheritance(self):
        remembered = str(self.root / 'old/app')
        self.assertEqual(self.run_policy(remembered, executable='old-policy.exe'), remembered)

    def test_new_install(self):
        self.assertEqual(self.run_policy(), self.default)

    def test_stale_temp(self):
        self.assertEqual(self.run_policy(str(self.root / 'old/app')), self.default)

    def test_temp_root(self):
        self.assertEqual(self.run_policy(tempfile.gettempdir()), self.default)

    def test_temp_case_and_dot_segments(self):
        self.assertEqual(self.run_policy(str(self.root / 'old/../app').upper()), self.default)

    def test_active_temp_requires_explicit_migration(self):
        self.run_policy(str(self.root / 'app'), active=True, expected_code=2)

    def test_durable_custom_location_preserved(self):
        custom = str(Path(os.environ['LOCALAPPDATA']) / 'My Apps/Horus')
        self.assertEqual(self.run_policy(custom), custom)

    def test_sibling_temp_prefix_is_not_temp(self):
        custom = tempfile.gettempdir() + '-durable\\Horus'
        self.assertEqual(self.run_policy(custom), custom)

    def test_explicit_directory_preserved(self):
        target = str(self.root / 'explicit app')
        self.assertEqual(self.run_policy(str(self.root / 'old'), explicit=target), target)

class StaticContract(unittest.TestCase):
    def test_user_scope_and_custom_template(self):
        import json
        nsis = json.loads((REPO / 'frontend/src-tauri/tauri.conf.json').read_text())['bundle']['windows']['nsis']
        self.assertEqual(nsis['installMode'], 'currentUser')
        self.assertEqual(nsis['template'], 'installer.nsi')

    def test_uninstall_has_no_data_deletion_control_or_operation(self):
        source = TEMPLATE.read_text()
        self.assertNotIn('DeleteAppDataCheckbox', source)
        self.assertNotIn('$(deleteAppData)', source)
        self.assertNotIn('RmDir /r', source)
        self.assertNotIn('RMDir /r', source)

    def test_no_hooks_can_reintroduce_deletion(self):
        import json
        nsis = json.loads((REPO / 'frontend/src-tauri/tauri.conf.json').read_text())['bundle']['windows']['nsis']
        self.assertNotIn('installerHooks', nsis)


if __name__ == '__main__':
    unittest.main()
