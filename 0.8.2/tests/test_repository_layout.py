from pathlib import Path
import unittest


VERSION_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = VERSION_ROOT.parent


class RepositoryLayoutTests(unittest.TestCase):
    def test_client_versions_have_parallel_top_level_directories(self):
        self.assertEqual('0.8.2', VERSION_ROOT.name)
        self.assertTrue((PROJECT_ROOT / '0.8.2' / 'README.md').is_file())
        self.assertTrue((PROJECT_ROOT / '0.9.22' / 'README.md').is_file())

    def test_082_runtime_and_tools_are_version_local(self):
        for name in ('gui', 'native', 'scripts', 'tests', 'tools'):
            self.assertTrue((VERSION_ROOT / name).is_dir(), name)
            self.assertFalse((PROJECT_ROOT / name).exists(), name)

    def test_legal_files_remain_shared_at_project_root(self):
        for name in ('LICENSE', 'THIRD_PARTY_NOTICES.md', 'licenses'):
            self.assertTrue((PROJECT_ROOT / name).exists(), name)
            self.assertFalse((VERSION_ROOT / name).exists(), name)


if __name__ == '__main__':
    unittest.main()
