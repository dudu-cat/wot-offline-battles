import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
PORT_ROOT = ROOT / '0.9.22'
PACKAGE_ROOT = (
    PORT_ROOT / 'src' / 'res' / 'scripts' / 'client' / 'gui' / 'mods' /
    'offline_lan_0922')


def _load_source_audit():
    path = PORT_ROOT / 'tools' / 'audit_battle_sources.py'
    spec = importlib.util.spec_from_file_location(
        'audit_battle_sources_release_test', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ReleaseLayoutTests(unittest.TestCase):
    def test_port_has_one_top_level_layout_and_64_documented_modules(self):
        self.assertTrue(PORT_ROOT.is_dir())
        self.assertFalse((ROOT / 'ports' / '0.9.22').exists())

        actual = {
            path.relative_to(PACKAGE_ROOT).as_posix()
            for path in PACKAGE_ROOT.rglob('*.py')
        }
        audit = _load_source_audit()
        self.assertEqual(64, len(actual))
        self.assertEqual(actual, set(audit.PORT_FILES))

    def test_release_surfaces_do_not_reference_the_retired_nested_path(self):
        retired_path = '/'.join(('ports', '0.9.22'))
        paths = (
            ROOT / 'README.md',
            ROOT / '0.8.2' / 'LAN_SERVER.md',
            ROOT / 'THIRD_PARTY_NOTICES.md',
            ROOT / '.github' / 'workflows' / 'tests.yml',
            PORT_ROOT / 'README.md',
            PORT_ROOT / 'INSTALL.txt',
            PORT_ROOT / 'BATTLE_SOURCE_AUDIT.md',
            PORT_ROOT / 'build_for_client.sh',
            PORT_ROOT / 'build_wotmod.py',
        )
        for path in paths:
            self.assertNotIn(
                retired_path, path.read_text(encoding='utf-8'), str(path))

    def test_release_builder_has_fixed_loopback_default_and_no_host_override(self):
        source = (PORT_ROOT / 'build_wotmod.py').read_text(encoding='utf-8')
        self.assertIn("'host': '127.0.0.1'", source)
        self.assertIn("'port': 28782", source)
        self.assertNotIn('os.environ.get(', source)
        self.assertNotIn('server_endpoint.json', source)

        wrapper = (PORT_ROOT / 'build_for_client.sh').read_text(
            encoding='utf-8')
        self.assertIn('export PYTHONDONTWRITEBYTECODE=1', wrapper)

    def test_release_checklist_covers_native_and_windows_publish_gates(self):
        checklist = (
            PORT_ROOT / 'RELEASE_CHECKLIST.md').read_text(encoding='utf-8')
        for required in (
                '0.9.22.0.1 #1513',
                'CPython 2.7.18',
                'validate_wotmod.py',
                '0.0.0.0:28782',
                'SmartScreen',
                'UAC prompt',
                'Native Windows #1513 acceptance',
                'git push origin v0.4.0'):
            self.assertIn(required, checklist)


if __name__ == '__main__':
    unittest.main()
