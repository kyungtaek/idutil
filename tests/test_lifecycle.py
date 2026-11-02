import concurrent.futures
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from idutil import core, integration
from idutil.cli import parser, doctor
from idutil.storage import Error, Store, git_info, read_json, run, write_json


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name).resolve()
        self.repo = self.base / "repo with ' quotes"
        self.repo.mkdir()
        run(["git", "init", "-b", "main", str(self.repo)])
        (self.repo / "App.xcodeproj").mkdir()
        (self.repo / "App.xcodeproj/project.pbxproj").write_text("fixture")
        (self.repo / "Local").mkdir()
        (self.repo / "Local/code.swift").write_text("v1")
        run(["git", "-C", str(self.repo), "add", "."])
        run(["git", "-C", str(self.repo), "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-m", "fixture"])
        self.store = Store(self.base / "state")
        self.wtconfig = self.base / "wt.toml"
        self.wtconfig.write_text('# preserved comment\n[pre-start]\nuser = "echo user"\n')
        self.setup_args = ["setup", "--worktree", str(self.repo), "--ios", "--project", "App.xcodeproj", "--scheme", "App",
                           "--input", "Local", "--wt-config", str(self.wtconfig), "--retention-hours", "0"]
        self.register()

    def tearDown(self):
        self.temp.cleanup()

    def register(self, extra=None):
        args = parser().parse_args(self.setup_args + (extra or []))
        with patch("idutil.integration.shutil.which", return_value="wt"), patch("idutil.integration.run", return_value="wt unit-fixture"):
            return integration.setup(self.store, args)

    def prepare(self, path=None, **kw):
        return core.prepare(self.store, path or self.repo, **kw)["buildContext"]

    def worktree(self, name="feature"):
        path = self.base / name
        run(["git", "-C", str(self.repo), "worktree", "add", "-b", name, str(path)])
        return path

    def test_reuse_switch_and_independent_worktrees(self):
        a = self.prepare()
        bpath = self.worktree()
        b = self.prepare(bpath)
        self.assertNotEqual(a["derivedDataPath"], b["derivedDataPath"])
        self.assertEqual(a["resourceId"], self.prepare()["resourceId"])
        self.assertTrue((bpath / ".agents/skills/ios-disk-util/SKILL.md").exists())
        self.assertEqual(core.status(self.store, bpath)["buildContext"]["resourceId"], b["resourceId"])

    def test_recovery_managed_and_compare_and_swap(self):
        old = self.prepare()
        new = self.prepare(fresh=True, expected=old["resourceId"], reason="cache-corrupt")
        again = self.prepare(fresh=True, expected=old["resourceId"], reason="cache-corrupt")
        self.assertNotEqual(old["resourceId"], new["resourceId"])
        self.assertEqual(new["resourceId"], again["resourceId"])
        self.assertTrue(Path(old["derivedDataPath"]).exists())
        self.assertEqual(len(self.store.read()["resources"]), 2)
        self.assertEqual(self.store.read()["resources"][old["resourceId"]]["status"], "retired")

    def test_declared_same_mtime_change_rotates(self):
        old = self.prepare()
        p = self.repo / "Local/code.swift"
        s = p.stat()
        p.write_text("v2")
        os.utime(p, ns=(s.st_atime_ns, s.st_mtime_ns))
        new = self.prepare()
        self.assertNotEqual(old["resourceId"], new["resourceId"])
        self.assertEqual(self.store.read()["resources"][new["resourceId"]]["reason"], "declared-input-changed")

    def test_symlink_derived_data_recovers_without_touching_target(self):
        a = self.prepare()
        target = self.base / "unmanaged"
        target.mkdir()
        (target / "keep").write_text("user data")
        dd = Path(a["derivedDataPath"])
        dd.rmdir()
        dd.symlink_to(target, target_is_directory=True)
        b = self.prepare()
        self.assertNotEqual(a["resourceId"], b["resourceId"])
        self.assertEqual((target / "keep").read_text(), "user data")

    def test_removed_worktree_does_not_break_other_cache(self):
        bpath = self.worktree()
        a = self.prepare()
        b = self.prepare(bpath)
        core.check_release(self.store, bpath)
        self.assertEqual(core.release(self.store, bpath)["resourceIds"], [])
        run(["git", "-C", str(self.repo), "worktree", "remove", str(bpath)])
        self.assertEqual(core.release(self.store, bpath)["resourceIds"], [b["resourceId"]])
        plan = core.gc(self.store)
        self.assertEqual([x["resourceId"] for x in plan["items"]], [b["resourceId"]])
        with patch("idutil.core.busy_build_tools", return_value=[]):
            result = core.gc(self.store, plan["planId"], quiescent=True)
        self.assertEqual(result["removed"], [b["resourceId"]])
        deleted = next(r for r in core.status(self.store, measure=True)["resources"] if r["id"] == b["resourceId"])
        self.assertEqual(deleted["logicalBytes"], 0)
        self.assertNotIn("diagnostic", deleted)
        self.assertTrue(Path(a["derivedDataPath"]).exists())
        self.assertEqual(self.prepare()["resourceId"], a["resourceId"])

    def test_quiescence_and_changes_since_plan_protect(self):
        old = self.prepare()
        self.prepare(fresh=True)
        plan = core.gc(self.store)
        with self.assertRaises(Error):
            core.gc(self.store, plan["planId"])
        (Path(old["derivedDataPath"]) / "new-build").write_text("new")
        with patch("idutil.core.busy_build_tools", return_value=[]):
            result = core.gc(self.store, plan["planId"], quiescent=True)
        self.assertFalse(result["removed"])
        self.assertEqual(result["skipped"][0]["reason"], "changed-since-plan")

    def test_protected_xcresult_not_deleted(self):
        old = self.prepare()
        (Path(old["derivedDataPath"]) / "Failure.xcresult").mkdir()
        self.prepare(fresh=True)
        plan = core.gc(self.store)
        self.assertFalse(plan["items"])
        self.assertIn("protected-artifacts", [x["reason"] for x in plan["skipped"]])

    def test_active_build_tool_veto(self):
        self.prepare()
        self.prepare(fresh=True)
        plan = core.gc(self.store)
        with patch("idutil.core.busy_build_tools", return_value=["xcodebuild"]), self.assertRaises(Error):
            core.gc(self.store, plan["planId"], True)

    def test_late_release_does_not_touch_recreated_worktree(self):
        p = self.worktree()
        old = self.prepare(p)
        core.check_release(self.store, p)
        run(["git", "-C", str(self.repo), "worktree", "remove", str(p)])
        run(["git", "-C", str(self.repo), "worktree", "add", str(p), "feature"])
        new = self.prepare(p)
        self.assertNotEqual(old["worktreeId"], new["worktreeId"])
        self.assertFalse(core.release(self.store, p)["resourceIds"])
        self.assertTrue(Path(new["derivedDataPath"]).exists())

    def test_read_only_status_doctor_and_dry_run(self):
        before = self.store.registry.read_bytes()
        core.status(self.store, self.repo, measure=True)
        args = parser().parse_args(["doctor", "--worktree", str(self.repo)])
        doctor(self.store, args)
        self.register(["--dry-run"])
        self.assertEqual(self.store.registry.read_bytes(), before)

    def test_dry_run_on_empty_root_makes_no_files(self):
        store = Store(self.base / "dry-run-root")
        args = parser().parse_args(self.setup_args + ["--dry-run", "--wt-config", str(self.base / "dry-wt.toml")])
        with patch("idutil.integration.shutil.which", return_value=None):
            integration.setup(store, args)
        self.assertFalse(store.root.exists())

    def test_install_repeated_preserves_user_hooks_and_rule_edits(self):
        first = self.wtconfig.read_text()
        self.register()
        self.assertEqual(self.wtconfig.read_text(), first)
        parsed = integration.tomlkit.parse(first)
        self.assertEqual(str(parsed["pre-start"][1]["user"]), "echo user")
        self.assertIn("# preserved comment", first)
        p = self.repo / ".agents/skills/ios-disk-util/SKILL.md"
        p.write_text(p.read_text() + "\nUser addition\n")
        report = self.register()
        self.assertTrue(report["warnings"])
        self.assertIn("User addition", p.read_text())

    def test_uninstall_preserves_user_text_and_caches(self):
        a = self.prepare()
        agents = self.repo / "AGENTS.md"
        agents.write_text("User rule\n\n" + agents.read_text())
        integration.uninstall(self.store, git_info(self.repo))
        self.assertEqual(agents.read_text().strip(), "User rule")
        self.assertTrue(Path(a["derivedDataPath"]).exists())
        text = self.wtconfig.read_text()
        self.assertIn("echo user", text)
        self.assertNotIn("idutil =", text)

    def test_registry_corruption_does_not_reset_or_delete(self):
        a = self.prepare()
        self.store.registry.write_text("{")
        with self.assertRaises(Error):
            self.prepare()
        self.assertTrue(Path(a["derivedDataPath"]).is_dir())
        self.assertEqual(self.store.registry.read_text(), "{")

    def test_runtime_survives_source_worktree_removal(self):
        data = self.store.read()
        exe = data["repositories"][git_info(self.repo)["repositoryId"]]["runtime"]
        result = run([exe, "--root", str(self.store.root), "status", "--json"])
        self.assertEqual(json.loads(result)["schemaVersion"], 1)
        self.assertFalse(str(exe).startswith(str(self.repo)))

    def test_parallel_prepare_single_generation(self):
        env = dict(os.environ)
        commands = [sys.executable, "-m", "idutil", "--root", str(self.store.root), "prepare", "--worktree", str(self.repo), "--json"]
        def invoke(_):
            return json.loads(run(commands, env=env))["buildContext"]["resourceId"]
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            ids = list(pool.map(invoke, range(4)))
        self.assertEqual(len(set(ids)), 1)
        self.assertEqual(len(self.store.read()["resources"]), 1)

    def test_effective_mcp_path_conflict_diagnosed(self):
        self.prepare()
        p = self.base / "defaults.json"
        write_json(p, {"derivedDataPath": "/tmp/wrong"})
        args = parser().parse_args(["doctor", "--worktree", str(self.repo), "--effective-settings", str(p)])
        result = doctor(self.store, args)
        self.assertIn("effective-setting-conflict", [x["code"] for x in result["findings"]])

    def test_worktree_protected_artifact_blocks_removal_check_only(self):
        self.register(["--protect", "results"])
        (self.repo / "results").mkdir()
        a = self.prepare()
        with self.assertRaises(Error):
            core.check_release(self.store, self.repo)
        self.assertTrue(Path(a["derivedDataPath"]).exists())

    def test_gc_replaced_resource_and_symlink_target_protected(self):
        a = self.prepare()
        self.prepare(fresh=True)
        plan = core.gc(self.store)
        path = Path(a["derivedDataPath"]).parent
        moved = path.with_name(path.name + "-moved")
        path.rename(moved)
        external = self.base / "external"
        external.mkdir()
        (external / "precious").write_text("keep")
        path.symlink_to(external, target_is_directory=True)
        with patch("idutil.core.busy_build_tools", return_value=[]):
            result = core.gc(self.store, plan["planId"], True)
        self.assertFalse(result["removed"])
        self.assertEqual((external / "precious").read_text(), "keep")

    def test_update_preserves_registered_policy(self):
        self.register(["--protect", "release", "--retention-hours", "72", "--agent", "claude"])
        args = parser().parse_args(["setup", "--worktree", str(self.repo), "--wt-config", str(self.wtconfig), "--update"])
        with patch("idutil.integration.shutil.which", return_value="wt"), patch("idutil.integration.run", return_value="wt fixture"):
            integration.setup(self.store, args)
        repo = self.store.read()["repositories"][git_info(self.repo)["repositoryId"]]
        self.assertEqual(repo["protectedPaths"], ["release"])
        self.assertEqual(repo["retentionHours"], 72)
        self.assertEqual(repo["agents"], ["claude"])

    def test_unavailable_shared_cache_falls_back_and_stays_managed(self):
        self.register(["--shared-cache"])
        with patch("idutil.core.toolchain", side_effect=Error("SDK unavailable")):
            result = core.prepare(self.store, self.repo)
        self.assertEqual(result["buildContext"]["cacheMode"], "private-only")
        self.assertTrue(result["warnings"])
        self.assertIn(result["buildContext"]["resourceId"], self.store.read()["resources"])
        bypass = self.prepare(no_shared=True)
        self.assertTrue(self.store.read()["resources"][bypass["resourceId"]]["sharedCacheDisabled"])

    def test_resource_identifier_cannot_escape_root(self):
        with self.assertRaises(Error):
            self.store.resource_path({"id": "../external", "path": str(self.base / "external")})

    def test_shared_cache_bypass_survives_generation_changes(self):
        self.register(["--shared-cache"])
        tc = {"version": "Xcode 27.0\nBuild fixture", "developerDir": "/fixture", "simulatorSDK": "fixture", "hostArchitecture": "arm64"}
        with patch("idutil.core.toolchain", return_value=tc):
            old = self.prepare()
            self.assertEqual(old["cacheMode"], "shared")
            recovered = self.prepare(fresh=True, expected=old["resourceId"], no_shared=True)
            (self.repo / "Local/code.swift").write_text("v2")
            changed = self.prepare()
            self.assertNotEqual(changed["resourceId"], recovered["resourceId"])
            self.assertEqual(changed["cacheMode"], "private-only")
            self.register(["--shared-cache"])
            self.assertEqual(self.prepare()["cacheMode"], "shared")


if __name__ == "__main__":
    unittest.main()
