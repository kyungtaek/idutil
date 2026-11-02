"""Real Worktrunk contract tests. No user configuration is used."""
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from idutil import core, integration
from idutil.cli import parser
from idutil.storage import Store, run

WT = Path(os.environ.get("IDUTIL_TEST_WT", ".tools/worktrunk/wt")).resolve()


@unittest.skipUnless(WT.is_file(), "Set IDUTIL_TEST_WT to a real Worktrunk binary")
class WorktrunkIntegration(unittest.TestCase):
    def test_create_switch_remove_and_non_registered_noop(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp).resolve()
            repo = base / "project with spaces"
            run(["git", "init", "-b", "main", str(repo)])
            (repo / "App.xcodeproj").mkdir()
            (repo / "App.xcodeproj/project.pbxproj").write_text("fixture")
            run(["git", "-C", str(repo), "add", "."])
            run(["git", "-C", str(repo), "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-m", "fixture"])
            store = Store(base / "managed data")
            env = {**os.environ, "PATH": str(WT.parent) + os.pathsep + os.environ["PATH"],
                   "WORKTRUNK_CONFIG_PATH": str(base / "wt.toml"),
                   "WORKTRUNK_SYSTEM_CONFIG_PATH": str(base / "no-system.toml"),
                   "XDG_CONFIG_HOME": str(base / "xdg"), "NO_COLOR": "1"}
            args = parser().parse_args(["setup", "--ios", "--worktree", str(repo), "--project", "App.xcodeproj",
                                       "--scheme", "App", "--wt-config", env["WORKTRUNK_CONFIG_PATH"], "--retention-hours", "0"])
            with patch.dict(os.environ, env):
                integration.setup(store, args)
                a = core.prepare(store, repo)["buildContext"]
                bpath = base / "feature 'quoted' space"
                run([str(WT), "-C", str(repo), "switch", "--create", "feature", "--base", "main", "--path", str(bpath), "--no-cd"], env=env)
                b = core.status(store, bpath)["buildContext"]
                self.assertIsNotNone(b, "pre-start must prepare the worktree before switch returns")
                self.assertNotEqual(a["derivedDataPath"], b["derivedDataPath"])
                self.assertTrue((bpath / ".claude/skills/ios-disk-util/SKILL.md").is_file())
                run([str(WT), "-C", str(repo), "switch", "feature", "--no-cd"], env=env)
                self.assertEqual(core.prepare(store, bpath)["buildContext"]["resourceId"], b["resourceId"])
                run([str(WT), "-C", str(repo), "remove", "--foreground", "--no-delete-branch", "feature"], env=env)
                deadline = time.monotonic() + 5
                while store.read()["resources"][b["resourceId"]]["status"] != "released" and time.monotonic() < deadline:
                    time.sleep(.05)
                self.assertEqual(store.read()["resources"][b["resourceId"]]["status"], "released")
                self.assertTrue(Path(a["derivedDataPath"]).exists())
                # User hooks run globally, but an unrelated repo must remain untouched.
                unrelated = base / "unregistered"
                run(["git", "init", "-b", "main", str(unrelated)])
                run(["git", "-C", str(unrelated), "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "--allow-empty", "-m", "fixture"])
                cpath = base / "unregistered-feature"
                before = store.registry.read_bytes()
                run([str(WT), "-C", str(unrelated), "switch", "--create", "other", "--base", "main", "--path", str(cpath), "--no-cd"], env=env)
                self.assertEqual(before, store.registry.read_bytes())
                self.assertFalse((cpath / "AGENTS.md").exists())
