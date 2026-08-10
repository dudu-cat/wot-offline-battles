from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class LicensingTests(unittest.TestCase):
    def test_project_declares_gpl_without_licensing_wargaming_assets(self):
        license_text = (ROOT / 'LICENSE').read_text(encoding='utf-8')
        notices = (ROOT / 'THIRD_PARTY_NOTICES.md').read_text(
            encoding='utf-8')

        self.assertIn('GNU GENERAL PUBLIC LICENSE', license_text)
        self.assertIn('Version 3, 29 June 2007', license_text)
        self.assertIn('does not grant rights to Wargaming', notices)

if __name__ == '__main__':
    unittest.main()
