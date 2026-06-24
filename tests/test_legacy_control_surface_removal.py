from __future__ import annotations

import unittest
from pathlib import Path

from academic_engine.autonomous_runtime_store import autonomous_runtime_dir
from academic_engine.state import RuntimeStore


class LegacyControlSurfaceRemovalTests(unittest.TestCase):
    def test_legacy_control_surface_files_are_absent(self) -> None:
        root = Path(__file__).resolve().parents[1]
        old_transport = "tele" + "gram"
        renamed_package = "academic" + "_engine"
        launchd_basename = renamed_package.replace("_", "-")
        forbidden_paths = (
            "academic_engine/agent_chat.py",
            "academic_engine/bot.py",
            "academic_engine/chat_wrapper.py",
            "academic_engine/config.py",
            "academic_engine/email_delivery.py",
            "academic_engine/launchd_service.py",
            "academic_engine/projects.py",
            "academic_engine/prompting.py",
            f"academic_engine/{old_transport}_api.py",
            f"deploy/local-{launchd_basename}.plist",
            f"scripts/{renamed_package}.py",
            f"scripts/run_{renamed_package}_launchd.sh",
            f"output/{old_transport}/README.md",
        )

        for relative_path in forbidden_paths:
            with self.subTest(path=relative_path):
                self.assertFalse((root / relative_path).exists())

    def test_runtime_paths_use_current_namespace(self) -> None:
        with self.subTest(component="workflow-runtime-store"):
            runtime_dir = RuntimeStore(Path.cwd()).runtime_dir
            self.assertEqual(runtime_dir, Path.cwd().resolve() / "output" / "runtime")

        with self.subTest(component="autonomous-daemon-runtime"):
            runtime_dir = autonomous_runtime_dir(Path.cwd())
            self.assertEqual(runtime_dir, Path.cwd().resolve() / "output" / "runtime" / "autonomous")


if __name__ == "__main__":
    unittest.main()
