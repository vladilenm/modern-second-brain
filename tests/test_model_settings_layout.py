import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


@unittest.skipUnless(sys.platform == "darwin" and shutil.which("xcrun"), "нужны macOS и Swift")
class ModelSettingsLayoutTests(unittest.TestCase):
    def test_switching_near_screen_edge_keeps_buttons_visible(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            binary = Path(tmp) / "layout-test"
            subprocess.run(["xcrun", "swiftc", "-swift-version", "5", "-parse-as-library",
                            "-module-cache-path", str(Path(tmp) / "cache"), "-o", str(binary),
                            str(root / "menubar/MenuViews.swift"), str(root / "menubar/ModelSettings.swift"),
                            str(root / "tests/model_settings_layout.swift")], check=True, capture_output=True, timeout=120)
            subprocess.run([str(binary)], check=True, capture_output=True, timeout=15)
