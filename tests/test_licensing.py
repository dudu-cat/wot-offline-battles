import importlib.util
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
PORT_ROOT = ROOT / 'ports' / '0.9.22'


class LicensingTests(unittest.TestCase):
    def test_project_declares_gpl_without_licensing_wargaming_assets(self):
        license_text = (ROOT / 'LICENSE').read_text(encoding='utf-8')
        notices = (ROOT / 'THIRD_PARTY_NOTICES.md').read_text(
            encoding='utf-8')

        self.assertIn('GNU GENERAL PUBLIC LICENSE', license_text)
        self.assertIn('Version 3, 29 June 2007', license_text)
        self.assertIn('does not grant rights to Wargaming', notices)

    def test_0922_packager_copies_required_legal_files(self):
        packager_path = PORT_ROOT / 'build_wotmod.py'
        spec = importlib.util.spec_from_file_location(
            'build_wotmod_licensing_test', packager_path)
        packager = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(packager)

        with tempfile.TemporaryDirectory() as directory:
            packager._copy_legal_files(directory)
            destination = Path(directory)

            self.assertTrue((destination / 'LICENSE').is_file())
            self.assertTrue(
                (destination / 'THIRD_PARTY_NOTICES.md').is_file())
            self.assertTrue(
                (destination / 'licenses' / 'Boost-1.0.txt').is_file())


if __name__ == '__main__':
    unittest.main()
