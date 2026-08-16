from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
PORT_ROOT = ROOT / '0.9.22'


class ReleaseBuilderTests(unittest.TestCase):
    def test_the_package_defaults_to_loopback_and_keeps_user_settings(self):
        source = (PORT_ROOT / 'build_wotmod.py').read_text(encoding='utf-8')
        self.assertIn("'host': '127.0.0.1'", source)
        self.assertIn("'port': 28782", source)
        self.assertNotIn('os.environ.get(', source)
        self.assertNotIn('server_endpoint.json', source)


if __name__ == '__main__':
    unittest.main()
