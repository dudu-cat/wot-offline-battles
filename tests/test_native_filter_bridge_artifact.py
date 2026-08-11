import importlib.util
import io
import unittest
from contextlib import redirect_stdout
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
AUDITOR_PATH = ROOT / "tools/audit_native_filter_bridge.py"
PYD_PATH = (
    ROOT / "scripts/client/gui/mods/offhangar/offhangar_native_seed.pyd"
)


class NativeFilterBridgeArtifactTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location(
            "audit_native_filter_bridge_under_test", AUDITOR_PATH
        )
        cls.auditor = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.auditor)

    def test_shipped_pyd_is_x86_and_has_only_expected_export(self):
        self.assertTrue(PYD_PATH.is_file())
        output = io.StringIO()
        with redirect_stdout(output):
            self.auditor.audit_bridge(PYD_PATH)
        self.assertIn("PYD OK", output.getvalue())


if __name__ == "__main__":
    unittest.main()
