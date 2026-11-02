"""Reversible, ownership-tracked installation. No agent runtime or build wrapper."""
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import sys
import uuid

import tomlkit

from . import __version__
from .storage import Error, atomic_write, digest, git_info, no_symlinks, read_json, run, write_json


BEGIN = "<!-- idutil:begin -->"
END = "<!-- idutil:end -->"


def runtime_source():
    return Path(__file__).parent


def runtime_path(store):
    if getattr(sys, "frozen", False):
        # The extraction directory of a one-file executable disappears at exit.
        # Key the durable hook runtime by the executable, never that directory.
        h = hashlib.sha256()
        with Path(sys.executable).open("rb") as executable:
            for block in iter(lambda: executable.read(1024 * 1024), b""):
                h.update(block)
        return store.root / "runtime" / (__version__ + "-" + h.hexdigest()[:12])
    src = runtime_source()
    content = "".join(p.read_text() for p in sorted(src.rglob("*")) if p.is_file() and p.suffix in (".py", ".md"))
    return store.root / "runtime" / (__version__ + "-" + digest(content)[:12])


def install_runtime(store):
    dest = no_symlinks(runtime_path(store))
    if not dest.exists():
        stage = dest.with_name(".staging-" + uuid.uuid4().hex)
        stage.mkdir(parents=True, mode=0o700)
        if getattr(sys, "frozen", False):
            shutil.copy2(sys.executable, stage / "idutil-cli")
            (stage / "idutil-cli").chmod(0o700)
        else:
            shutil.copytree(runtime_source(), stage / "idutil", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            shutil.copytree(Path(tomlkit.__file__).parent, stage / "tomlkit", ignore=shutil.ignore_patterns("__pycache__"))
            # Real interpreter path survives removal of the source project's virtualenv.
            executable = str(Path(sys.executable).resolve())
            launch = "#!/bin/sh\nexec " + shlex.quote(executable) + " " + shlex.quote(str(dest / "entry.py")) + ' "$@"\n'
            atomic_write(stage / "entry.py", "from idutil.cli import main\nmain()\n")
            atomic_write(stage / "idutil-cli", launch, mode=0o700)
        write_json(stage / "complete.json", {"version": __version__})
        os.rename(stage, dest)
    if not (dest / "complete.json").is_file():
        raise Error("Incomplete runtime; reinstall to a clean management root or inspect runtime staging")
    return dest / "idutil-cli"


def _replace_block(text, block):
    if BEGIN in text or END in text:
        if text.count(BEGIN) != 1 or text.count(END) != 1 or text.index(BEGIN) > text.index(END):
            raise Error("Malformed idutil rule block")
        a, b = text.index(BEGIN), text.index(END) + len(END)
        return text[:a] + block + text[b:]
    return text.rstrip() + ("\n\n" if text else "") + block + "\n"


def rule_files(store, info, repo):
    exe = repo["runtime"]
    prefix = shlex.quote(exe) + " --root " + shlex.quote(str(store.root))
    skill = (runtime_source() / "assets/ios-disk-util/SKILL.md").read_text().replace("@IDUTIL@", prefix)
    block = BEGIN + "\n" + (
        "For this registered iOS repository, manage worktrees with Worktrunk (wt). "
        "Before build/test or after switching worktrees, read the ios-disk-util skill and run "
        "`" + prefix + " prepare --worktree <absolute-worktree-path> --json`. "
        "Apply its buildContext to the existing xcodebuild/XcodeBuildMCP tool. "
        "Use managed recovery for missing/conflicting caches; never fall back to default DerivedData. "
        "idutil does not execute builds or remove worktrees.\n") + END
    files = {}
    for agent in repo["agents"]:
        directory = ".agents" if agent == "codex" else ".claude"
        files[directory + "/skills/ios-disk-util/SKILL.md"] = ("file", skill)
        files["AGENTS.md" if agent == "codex" else "CLAUDE.md"] = ("block", block)
    return files


def install_worktree_rules(store, data, info, repo, dry_run=False):
    if not repo.get("agents"):
        return []
    manifest = data["installations"].setdefault(info["worktreeId"], {"path": info["path"], "files": {}})
    warnings = []
    for relative, (kind, content) in rule_files(store, info, repo).items():
        try:
            path = no_symlinks(Path(info["path"]) / relative)
        except Error as exc:
            warnings.append("Integration not written: " + str(exc))
            continue
        old = path.read_text() if path.exists() else ""
        owned = manifest["files"].get(relative)
        if owned:
            actual = old
            if kind == "block" and BEGIN in old and END in old:
                actual = old[old.index(BEGIN):old.index(END) + len(END)]
            if digest(actual) != owned["hash"]:
                warnings.append("User-modified integration preserved: " + str(path))
                continue
        elif old and ((kind == "file" and old != content) or (kind == "block" and BEGIN in old)):
            warnings.append("Existing integration preserved: " + str(path))
            continue
        updated = _replace_block(old, content) if kind == "block" else content
        if not dry_run and old != updated:
            if not owned and old:
                write_json(store.root / "backups" / (digest(str(path)) + ".json"), {"path": str(path), "content": old})
            atomic_write(path, updated)
        manifest["files"][relative] = {"kind": kind, "hash": digest(content), "created": owned["created"] if owned else not bool(old)}
    # Local ignore entries keep generated instructions out of worktree-removal/untracked checks.
    # Only ignore newly created files, never a pre-existing/tracked instruction file.
    excludes = ["/" + p for p, v in manifest["files"].items() if v["created"]]
    ep = no_symlinks(Path(info["common"]) / "info/exclude")
    old_exclude = ep.read_text() if ep.exists() else ""
    missing = [p for p in excludes if p not in old_exclude.splitlines()]
    if missing and not dry_run:
        atomic_write(ep, old_exclude.rstrip() + "\n" + "\n".join(missing) + "\n")
        manifest.setdefault("excludesAdded", []).extend(missing)
    return warnings


def hook_commands(store, exe):
    base = shlex.quote(str(exe)) + " --root " + shlex.quote(str(store.root)) + " _hook "
    return {"pre-start": base + "prepare --worktree {{ worktree_path }} --json",
            "pre-remove": base + "check-release --worktree {{ worktree_path }} --json",
            "post-remove": base + "release --worktree {{ worktree_path }} --json"}


def merge_hooks(text, commands, previous=None, remove=False):
    """Preserve comments and other commands; prepend sequential steps instead of racing."""
    doc = tomlkit.parse(text)
    previous = previous or {}
    for event, command in commands.items():
        current = doc.get(event)
        if (not remove and isinstance(current, tomlkit.items.AoT) and current
                and list(current[0].keys()) == ["idutil"] and str(current[0]["idutil"]) == command
                and not any("idutil" in step for step in current[1:])):
            continue
        steps = []
        if current is not None:
            if isinstance(current, (str, tomlkit.items.String)):
                t = tomlkit.table()
                t["existing"] = str(current)
                steps = [t]
            elif isinstance(current, tomlkit.items.AoT):
                steps = list(current)
            elif isinstance(current, (dict, tomlkit.items.Table)):
                steps = [current]
            else:
                raise Error("Unsupported Worktrunk hook shape: " + event)
        kept = []
        for step in steps:
            if "idutil" in step:
                if str(step["idutil"]) not in (command, previous.get(event)):
                    raise Error("User-modified idutil hook preserved: " + event)
                del step["idutil"]
            if len(step):
                kept.append(step)
        if not remove:
            first = tomlkit.table()
            first["idutil"] = command
            kept.insert(0, first)
        if event in doc:
            del doc[event]
        if kept:
            aot = tomlkit.aot()
            for step in kept:
                aot.append(step)
            doc[event] = aot
    return tomlkit.dumps(doc)


def setup(store, args):
    from .core import relative_path, target_path
    info = git_info(args.worktree)
    if store.root == Path(info["path"]) or store.root.is_relative_to(Path(info["path"])):
        raise Error("Management root must survive worktree removal; choose a path outside the worktree")
    if args.uninstall:
        return uninstall(store, info, args.dry_run)
    data = store.read()
    existing = data["repositories"].get(info["repositoryId"])
    if not existing and not args.ios:
        raise Error("Initial registration requires --ios (explicit project opt-in)")
    previous_config = (existing or {}).get("profiles", {}).get(args.profile, {})
    target = args.project or previous_config.get("target")
    scheme = args.scheme or previous_config.get("scheme")
    if not target or not scheme or not target.endswith((".xcodeproj", ".xcworkspace")):
        raise Error("Specify --project <relative.xcodeproj|relative.xcworkspace> and --scheme")
    config = {"target": relative_path(target), "scheme": scheme,
              "inputs": [relative_path(x) for x in args.inputs] if args.inputs is not None else previous_config.get("inputs", []),
              "developerDir": args.developer_dir or previous_config.get("developerDir"),
              "sharedCache": args.shared_cache if args.shared_cache is not None else previous_config.get("sharedCache", False),
              "sharedCacheEpoch": uuid.uuid4().hex if args.shared_cache is True else previous_config.get("sharedCacheEpoch")}
    target_path(info, config)
    retention = args.retention_hours if args.retention_hours is not None else (existing or {}).get("retentionHours", 24)
    if retention < 0:
        raise Error("Retention cannot be negative")
    wt = shutil.which("wt")
    wt_version = run([wt, "--version"]) if wt else None
    if not wt and not args.dry_run:
        raise Error("Worktrunk (wt) is required. Install it first; idutil will not install it silently")
    if args.agent is None:
        agent_names = (existing or {}).get("agents", ["codex", "claude"])
    else:
        agent_names = [] if args.agent == "none" else ["codex", "claude"] if args.agent == "all" else [args.agent]
    wtpath = no_symlinks(args.wt_config or os.environ.get("WORKTRUNK_CONFIG_PATH") or str(Path.home() / ".config/worktrunk/config.toml"))
    oldtext = wtpath.read_text() if wtpath.exists() else ""
    exe = runtime_path(store) / "idutil-cli"
    commands = hook_commands(store, exe)
    oldhooks = data.get("hookInstallation", {})
    if oldhooks and oldhooks["path"] != str(wtpath):
        raise Error("This root is already connected to a different Worktrunk config")
    newtext = merge_hooks(oldtext, commands, oldhooks.get("commands"))
    profiles = dict((existing or {}).get("profiles", {}))
    profiles[args.profile] = config
    repo = {"enabled": True, "common": info["common"], "commonIdentity": info["commonIdentity"],
            "profiles": profiles, "agents": agent_names, "runtime": str(exe),
            "retentionHours": retention,
            "protectedPaths": [relative_path(x) for x in args.protect] if args.protect is not None else (existing or {}).get("protectedPaths", []),
            "worktrunkVersion": wt_version}
    report = {"result": "planned" if args.dry_run else "installed", "repositoryId": info["repositoryId"],
              "root": str(store.root), "worktrunkVersion": wt_version, "worktrunkConfig": str(wtpath),
              "hookCommands": commands, "profile": config, "agentFiles": list(rule_files(store, info, repo)),
              "warnings": [] if wt else ["Worktrunk is not installed"],
              "automaticGC": "deferred: external writer quiescence is not established by hooks"}
    if args.dry_run:
        return report
    with store.locked() as latest:
        # Refuse racing external edits rather than clobbering them.
        if (wtpath.read_text() if wtpath.exists() else "") != oldtext:
            raise Error("Worktrunk configuration changed during setup")
        install_runtime(store)
        write_json(store.root / "backups" / (digest(str(wtpath)) + ".json"), {"path": str(wtpath), "content": oldtext})
        atomic_write(wtpath, newtext)
        latest["hookInstallation"] = {"path": str(wtpath), "commands": commands}
        latest["repositories"][info["repositoryId"]] = repo
        report["warnings"].extend(install_worktree_rules(store, latest, info, repo))
        store.save(latest)
        store.event("setup", repositoryId=info["repositoryId"])
    return report


def uninstall(store, info, dry_run=False):
    data = store.read()
    repo = data["repositories"].get(info["repositoryId"])
    if not repo:
        return {"result": "not-installed"}
    changes, warnings = [], []
    for wid, manifest in data["installations"].items():
        # Do not touch instructions at a reused path owned by a different worktree.
        try:
            current = git_info(manifest["path"])
        except (Error, OSError):
            continue
        if current["repositoryId"] != info["repositoryId"] or current["worktreeId"] != wid:
            continue
        for rel, owned in manifest["files"].items():
            path = no_symlinks(Path(manifest["path"]) / rel)
            if not path.exists():
                continue
            text = path.read_text()
            value = text
            if owned["kind"] == "block" and BEGIN in text and END in text:
                value = text[text.index(BEGIN):text.index(END) + len(END)]
            if digest(value) != owned["hash"]:
                warnings.append("User-modified file preserved: " + str(path))
                continue
            updated = text.replace(value, "").strip() + "\n" if owned["kind"] == "block" else ""
            changes.append((path, updated if updated.strip() else None))
    others = any(k != info["repositoryId"] and v.get("enabled") for k, v in data["repositories"].items())
    hi = data.get("hookInstallation")
    if not others and hi:
        p = no_symlinks(hi["path"])
        text = p.read_text() if p.exists() else ""
        changes.append((p, merge_hooks(text, hi["commands"], hi["commands"], remove=True)))
    if not dry_run:
        with store.locked() as latest:
            for p, updated in changes:
                if updated is None:
                    p.unlink()
                else:
                    atomic_write(p, updated)
            latest["repositories"][info["repositoryId"]]["enabled"] = False
            if not others:
                latest.pop("hookInstallation", None)
            store.save(latest)
    return {"result": "uninstall-planned" if dry_run else "uninstalled", "files": [str(x[0]) for x in changes],
            "warnings": warnings, "preserved": "Worktrunk, caches, worktrees, registry and local ignore entries"}
