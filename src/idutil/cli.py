import argparse
import json
import os
from pathlib import Path
import shutil
import sys

from . import __version__
from . import core, integration
from .storage import Error, Store, git_info, read_json


def parser():
    p = argparse.ArgumentParser(prog="idutil", description="Manage iOS build data alongside Worktrunk; never runs builds.")
    p.add_argument("--version", action="version", version="idutil " + __version__)
    p.add_argument("--root", default=os.environ.get("IDUTIL_ROOT", str(Path.home() / "Library/Application Support/idutil")),
                   help="Owned management directory outside worktrees")
    commands = p.add_subparsers(dest="command", required=True)
    setup = commands.add_parser("setup", help="Register an iOS repository and install reversible agent/hooks integration")
    setup.add_argument("--worktree", default=".")
    setup.add_argument("--ios", action="store_true", help="Explicitly opt this repository into iOS management")
    setup.add_argument("--project", help="Repository-relative .xcodeproj or .xcworkspace")
    setup.add_argument("--scheme")
    setup.add_argument("--profile", default="default")
    setup.add_argument("--input", dest="inputs", action="append", help="Declared local/vendored input path; repeatable")
    setup.add_argument("--protect", action="append", help="Worktree-relative protected output; replaces the list when supplied")
    setup.add_argument("--developer-dir")
    setup.add_argument("--shared-cache", action="store_true", default=None, help="Opt into validated native compilation cache")
    setup.add_argument("--private-cache", action="store_false", dest="shared_cache")
    setup.add_argument("--retention-hours", type=float, help="Defaults to 24 on first setup; preserved on update")
    setup.add_argument("--agent", choices=["all", "codex", "claude", "none"], help="Defaults to all on first setup; preserved on update")
    setup.add_argument("--scope", choices=["project"], default="project")
    setup.add_argument("--wt-config", help="Worktrunk USER config path (supports WORKTRUNK_CONFIG_PATH)")
    setup.add_argument("--dry-run", action="store_true")
    setup.add_argument("--update", action="store_true")
    setup.add_argument("--uninstall", action="store_true")
    setup.add_argument("--json", action="store_true")

    prepare = commands.add_parser("prepare", help="Reuse or recover a managed build space; no build execution")
    prepare.add_argument("--worktree", default=".")
    prepare.add_argument("--profile", default="default")
    prepare.add_argument("--fresh", action="store_true")
    prepare.add_argument("--expected-resource", help="Compare-and-swap recovery of this previous resource ID")
    prepare.add_argument("--reason")
    prepare.add_argument("--no-shared-cache", action="store_true")
    prepare.add_argument("--json", action="store_true")

    status = commands.add_parser("status", help="Read-only resource/context report")
    status.add_argument("--worktree")
    status.add_argument("--profile", default="default")
    status.add_argument("--measure", action="store_true", help="Measure managed resources (can be expensive)")
    status.add_argument("--json", action="store_true")
    doctor = commands.add_parser("doctor", help="Read-only diagnostics; never repairs or builds")
    doctor.add_argument("--worktree", default=".")
    doctor.add_argument("--profile", default="default")
    doctor.add_argument("--integration", action="store_true")
    doctor.add_argument("--effective-settings", help="JSON object of actual MCP defaults for comparison")
    doctor.add_argument("--build-settings", help="Actual xcodebuild -showBuildSettings -json output")
    doctor.add_argument("--json", action="store_true")
    gc = commands.add_parser("gc", help="Plan cleanup; apply only in an explicitly stopped build window")
    gc.add_argument("--apply")
    gc.add_argument("--quiescent", action="store_true", help="Attest related builds are stopped and remain stopped during GC")
    gc.add_argument("--json", action="store_true")
    hook = commands.add_parser("_hook", help="Internal Worktrunk adapter (not a worktree manager)")
    hook.add_argument("action", choices=["prepare", "check-release", "release"])
    hook.add_argument("--worktree", required=True)
    hook.add_argument("--json", action="store_true")
    return p


def doctor(store, args):
    result = core.status(store, args.worktree, args.profile)
    info = git_info(args.worktree)
    data = store.read()
    findings = []
    ctx = result["buildContext"]
    if not ctx:
        findings.append({"code": "needs-prepare", "message": "No usable binding; run prepare to allocate/recover managed data"})
    if not shutil.which("wt"):
        findings.append({"code": "worktrunk-missing", "message": "wt is not on PATH"})
    repo = data["repositories"].get(info["repositoryId"])
    if repo and args.profile in repo["profiles"]:
        evidence = core.input_evidence(info, repo["profiles"][args.profile])
        result["inputEvidence"] = evidence
        if ctx:
            old = data["resources"][ctx["resourceId"]]["inputs"]["inputs"]
            if old != evidence["inputs"]:
                findings.append({"code": "declared-input-changed", "message": "Re-run prepare; declared local/binary inputs changed"})
    if args.integration:
        hi = data.get("hookInstallation")
        if not hi or not Path(hi["path"]).exists():
            findings.append({"code": "hooks-missing", "message": "Run setup"})
        else:
            try:
                integration.merge_hooks(Path(hi["path"]).read_text(), hi["commands"], hi["commands"])
                # Presence must also be checked: merge alone can add missing hooks.
                text = Path(hi["path"]).read_text()
                parsed = integration.tomlkit.parse(text)
                for event, command in hi["commands"].items():
                    steps = parsed.get(event, [])
                    if not isinstance(steps, integration.tomlkit.items.AoT) or not any(str(x.get("idutil", "")) == command for x in steps):
                        findings.append({"code": "hook-missing-or-modified", "message": event})
            except (ValueError, Error) as exc:
                findings.append({"code": "hook-conflict", "message": str(exc)})
        result["hookApproval"] = "not-inferred; inspect Worktrunk trust settings"
    if ctx and args.effective_settings:
        settings = read_json(args.effective_settings)
        expected = ctx["mcpSessionDefaults"]
        for key in ("derivedDataPath", "workspacePath", "projectPath", "scheme"):
            if key in expected and settings.get(key) != expected[key]:
                findings.append({"code": "effective-setting-conflict", "message": key, "expected": expected[key], "actual": settings.get(key)})
        if settings.get(ctx["clearMCPDefault"]):
            findings.append({"code": "obsolete-project-default", "message": ctx["clearMCPDefault"]})
        if settings.get("extraArgs", []) != expected["extraArgs"]:
            findings.append({"code": "extra-args-require-review", "message": "Compare source package/cache paths and output overrides"})
        result["settingsEvidence"] = {"source": str(Path(args.effective_settings).resolve()), "scope": "provided snapshot, not live server"}
    if ctx and args.build_settings:
        rows = read_json(args.build_settings)
        if not isinstance(rows, list) or not rows:
            raise Error("Expected non-empty xcodebuild -showBuildSettings -json array")
        checked = 0
        for row in rows:
            settings = row.get("buildSettings", {})
            for key in ("BUILD_DIR", "OBJROOT", "TARGET_BUILD_DIR", "PROJECT_TEMP_DIR"):
                value = settings.get(key)
                if not value:
                    continue
                checked += 1
                if not Path(value).resolve().is_relative_to(Path(ctx["derivedDataPath"]).resolve()):
                    findings.append({"code": "output-outside-managed-derived-data", "target": row.get("target"), "message": key, "actual": value})
        if not checked:
            findings.append({"code": "no-output-settings", "message": "No output paths found"})
        result["buildSettingsEvidence"] = {"checkedPaths": checked, "scope": "settings snapshot; actual build output still needs verification"}
    result["findings"] = findings
    result["result"] = "attention" if findings else "ok"
    return result


def render(result):
    """Compact terminal view. JSON remains the complete automation contract."""
    if "resources" in result:
        print("RESOURCE      PROFILE      STATE       SIZE         WORKTREE")
        for r in result["resources"]:
            size = "unmeasured" if "logicalBytes" not in r else "{:.1f} MiB".format(r["logicalBytes"] / 1048576)
            print("{:<13} {:<12} {:<11} {:<12} {}".format(r["id"][:12], r["profile"], r["status"], size, r["worktreePath"]))
            if r.get("diagnostic"):
                print("  " + r["diagnostic"])
        ctx = result.get("buildContext")
        if ctx:
            print("DerivedData: " + ctx["derivedDataPath"])
        for f in result.get("findings", []):
            print(f["code"] + ": " + f["message"])
        print("Tool configuration/usage: unknown unless separately verified. Use --json for full context.")
    elif "buildContext" in result:
        ctx = result["buildContext"]
        print("{}: {} ({})".format(result["result"], ctx["resourceId"], "reused" if result.get("reused") else "new generation"))
        print("DerivedData: " + ctx["derivedDataPath"])
        print("Cache: " + ctx["cacheMode"] + "; apply the --json buildContext to your existing build tool.")
        for warning in result.get("warnings", []):
            print("Warning: " + warning)
    elif "planId" in result:
        print("GC plan: " + result["planId"])
        if "items" in result:
            print("{} candidates, {:.1f} MiB logical; {} held".format(len(result["items"]), result["logicalBytes"] / 1048576, len(result["skipped"])))
            print("After stopping build tools: idutil gc --apply " + result["planId"] + " --quiescent")
        else:
            print("Removed: {}; held: {}; free-space delta: {} bytes".format(len(result["removed"]), len(result["skipped"]), result["freeBytesDelta"]))
    else:
        print(json.dumps(result, indent=2, ensure_ascii=False))


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        store = Store(args.root)
        if args.command == "setup":
            result = integration.setup(store, args)
        elif args.command == "prepare":
            result = core.prepare(store, args.worktree, args.profile, args.fresh, args.expected_resource,
                                  args.reason, args.no_shared_cache)
        elif args.command == "status":
            result = core.status(store, args.worktree, args.profile, args.measure)
        elif args.command == "doctor":
            result = doctor(store, args)
        elif args.command == "gc":
            result = core.gc(store, args.apply, args.quiescent)
        elif args.action == "prepare":
            info = git_info(args.worktree)
            data = store.read()
            repo = data["repositories"].get(info["repositoryId"])
            if not repo or not repo.get("enabled"):
                result = {"result": "not-applicable"}
            else:
                result = {"profiles": {p: core.prepare(store, args.worktree, p, hook=True) for p in repo["profiles"]}}
        elif args.action == "check-release":
            result = core.check_release(store, args.worktree)
        else:
            result = core.release(store, args.worktree)
        if args.json:
            print(json.dumps({"schemaVersion": 1, **result}, indent=2, ensure_ascii=False))
        else:
            render(result)
        return 0
    except (Error, OSError, ValueError) as exc:
        payload = {"schemaVersion": 1, "result": "error", "error": str(exc)}
        print(json.dumps(payload, ensure_ascii=False), file=sys.stdout if args.json else sys.stderr)
        raise SystemExit(2)
