from pathlib import Path
import runpy
import tempfile
import unittest


VERSION_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = VERSION_ROOT.parent


class RepositoryLayoutTests(unittest.TestCase):
    def test_client_versions_have_parallel_top_level_directories(self):
        self.assertEqual('0.8.2', VERSION_ROOT.name)
        self.assertTrue((PROJECT_ROOT / '0.9.22').is_dir())

    def test_082_runtime_and_tools_are_version_local(self):
        for name in ('gui', 'native', 'scripts', 'tests', 'tools'):
            self.assertTrue((VERSION_ROOT / name).is_dir(), name)
            self.assertFalse((PROJECT_ROOT / name).exists(), name)

    def test_legal_files_remain_shared_at_project_root(self):
        for name in ('LICENSE', 'THIRD_PARTY_NOTICES.md', 'licenses'):
            self.assertTrue((PROJECT_ROOT / name).exists(), name)
            self.assertFalse((VERSION_ROOT / name).exists(), name)

    def test_packager_reads_identity_from_python_2_source(self):
        packager = runpy.run_path(
            str(VERSION_ROOT / 'tools' / 'package_native_experiment.py'))
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / 'legacy.py'
            source.write_text(
                "BUILD_ID = 'legacy-build'\n"
                "try:\n"
                "    pass\n"
                "except Exception, error:\n"
                "    pass\n",
                encoding='utf-8')
            self.assertEqual(
                'legacy-build', packager['_assigned_string'](source, 'BUILD_ID'))


if __name__ == '__main__':
    unittest.main()
