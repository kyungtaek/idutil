"""Build-space lifecycle. This module never builds, removes worktrees, or edits native CAS."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
import uuid

from .storage import Error, digest, git_info, identity, no_symlinks, read_json, run, tree_measure, worktree_presence, write_json


def key_for(info, profile):
    return info["worktreeId"] + ":" + profile


def relative_path(value):
    p = Path(value)
    if p.is_absolute() or ".." in p.parts or not p.parts:
        raise Error("Expected a repository-relative path: " + value)
    return str(p)


def target_path(info, config):
    path = Path(info["path"]) / config["target"]
    if not path.exists() or not path.resolve().is_relative_to(Path(info["path"])):
        raise Error("Project/workspace missing or outside this worktree: " + str(path))
    return path


def input_evidence(info, config):
    """Hash explicitly declared inputs, never claim a complete compiler dependency key."""
    root = Path(info["path"])
    candidates = list(config.get("inputs", []))
    target = Path(config["target"])
    for p in [Path("Package.resolved"), Path("Package.swift"), Path("Podfile"), Path("Podfile.lock"),
              Path("Gemfile.lock"), target / "project.pbxproj", target / "contents.xcworkspacedata",
              target / "xcshareddata/swiftpm/Package.resolved",
              target / "project.xcworkspace/xcshareddata/swiftpm/Package.resolved"]:
        if (root / p).is_file():
            candidates.append(str(p))
    evidence = {}
    for rel in sorted(set(candidates)):
        base = root / relative_path(rel)
        h = hashlib.sha256()
        try:
            if not base.exists() or base.is_symlink():
                raise Error("Missing or symlink input")
            files = [base] if base.is_file() else sorted(base.rglob("*"))
            for p in files:
                if p.is_symlink() or not p.resolve().is_relative_to(root):
                    raise Error("External/symlink input")
                if p.is_file():
                    before = p.stat()
                    h.update(str(p.relative_to(root)).encode() + b"\0")
                    h.update(str(before.st_mode & 0o777).encode() + b"\0")
                    with p.open("rb") as f:
                        for block in iter(lambda: f.read(1024 * 1024), b""):
                            h.update(block)
                    after = p.stat()
                    if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                        raise Error("Input changed while hashing")
            evidence[rel] = {"state": "observed", "sha256": h.hexdigest()}
        except (OSError, Error) as exc:
            evidence[rel] = {"state": "unknown", "reason": str(exc)}
    return {"observedAt": time.time(), "inputs": evidence, "completeDependencyGraph": False}


def toolchain(developer_dir):
    env = os.environ.copy()
    if developer_dir:
        env["DEVELOPER_DIR"] = developer_dir
    version = run(["/usr/bin/xcodebuild", "-version"], env=env)
    sdk = run(["/usr/bin/xcrun", "--sdk", "iphonesimulator", "--show-sdk-build-version"], env=env)
    return {"developerDir": developer_dir or run(["/usr/bin/xcode-select", "-p"]),
            "version": version, "simulatorSDK": sdk, "hostArchitecture": os.uname().machine}


def context(store, resource):
    path = store.resource_path(resource)
    config = resource["config"]
    project = str(Path(resource["worktree"]["path"]) / config["target"])
    dd = str(path / "DerivedData")
    spm = str(path / "SourcePackages")
    for child in ("DerivedData", "SourcePackages", "Artifacts"):
        no_symlinks(path / child)
        if not (path / child).is_dir():
            raise Error("Managed subdirectory missing: " + child)
    settings = ["COMPILATION_CACHE_ENABLE_CACHING=NO"]
    cache = resource.get("sharedCache")
    if cache:
        cachepath = no_symlinks(store.root / "shared" / cache["id"])
        if str(cachepath) != cache["path"] or not cachepath.is_dir():
            raise Error("Shared cache path changed")
        marker = read_json(no_symlinks(cachepath / "owner.json"))
        if marker.get("tool") != "idutil" or marker.get("id") != cache["id"]:
            raise Error("Shared cache ownership mismatch")
        no_symlinks(cachepath / "CAS")
        if not (cachepath / "CAS").is_dir():
            raise Error("Shared CAS directory missing")
        settings = ["COMPILATION_CACHE_ENABLE_CACHING=YES", "COMPILATION_CACHE_CAS_PATH=" + str(cachepath / "CAS")]
    extra = ["-clonedSourcePackagesDirPath", spm] + settings
    flag = "-workspace" if config["target"].endswith(".xcworkspace") else "-project"
    mcpkey = "workspacePath" if flag == "-workspace" else "projectPath"
    return {"schemaVersion": 1, "repositoryId": resource["repositoryId"],
            "worktreeId": resource["worktree"]["worktreeId"], "worktreePath": resource["worktree"]["path"],
            "resourceId": resource["id"], "generation": resource["generation"], "profile": resource["profile"],
            "derivedDataPath": dd, "sourcePackagesPath": spm, "artifactsPath": str(path / "Artifacts"),
            "xcodebuildArgs": [flag, project, "-scheme", config["scheme"], "-derivedDataPath", dd] + extra,
            "environment": {"DEVELOPER_DIR": config["developerDir"]} if config.get("developerDir") else {},
            "mcpSessionDefaults": {mcpkey: project, "scheme": config["scheme"], "derivedDataPath": dd, "extraArgs": extra},
            "clearMCPDefault": "projectPath" if mcpkey == "workspacePath" else "workspacePath",
            "cacheMode": "shared" if cache else "private-only",
            "cacheConnections": {"compilation": cache, "packageDownloads": "external-tool-owned; not garbage collected"},
            "adapterSupport": {"xcodebuild": "build/test with explicit managed paths",
                               "xcodebuildmcp": {"verifiedVersion": "2.7.0", "verifiedAction": "build_macos",
                                                "testAction": "use xcodebuild test; 2.7.0 drops derivedDataPath in test-without-building",
                                                "externalArtifacts": "MCP logs/results remain tool-owned; excluded from idutil GC",
                                                "otherVersionsAndActions": "require verification"},
                               "xcodeGUI": "not verified"},
            "configurationEvidence": "unknown", "usageEvidence": "unknown"}


def prepare(store, worktree, profile="default", fresh=False, expected=None, reason=None, no_shared=False, hook=False):
    info = git_info(worktree)
    with store.locked() as data:
        repo = data["repositories"].get(info["repositoryId"])
        if not repo or not repo.get("enabled"):
            if hook:
                return {"result": "not-applicable", "reason": "repository not registered"}
            raise Error("Register this iOS repository with idutil setup --ios first")
        config = repo["profiles"].get(profile)
        if not config:
            raise Error("Unknown profile: " + profile)
        # Older branches may not contain the selected iOS subproject.
        try:
            target_path(info, config)
        except Error:
            if hook:
                return {"result": "not-applicable", "reason": "registered target absent in this branch"}
            raise
        key = key_for(info, profile)
        oldid = data["bindings"].get(key)
        old = data["resources"].get(oldid)
        if expected and oldid != expected:
            # CAS-style recovery: a newer generation already won. Never rotate it again.
            if old:
                return {"result": "prepared", "recovered": False, "deduplicated": True, "buildContext": context(store, old)}
            raise Error("Recovery target changed; inspect status before retrying")
        recovery_key = key + ":" + (expected or "") + ":" + (reason or "")
        if expected and recovery_key in data["recoveries"]:
            r = data["resources"][data["recoveries"][recovery_key]]
            return {"result": "prepared", "deduplicated": True, "buildContext": context(store, r)}
        evidence = input_evidence(info, config)
        changed = bool(old and old.get("inputs", {}).get("inputs") != evidence["inputs"])
        warnings = []
        toolchain_changed = False
        if old and old.get("sharedCache"):
            try:
                toolchain_changed = toolchain(config.get("developerDir")) != old["sharedCache"]["toolchain"]
            except Error:
                toolchain_changed = True
        if old and not fresh and not (no_shared and not old.get("sharedCacheDisabled")) and not toolchain_changed and old["config"] == config:
            try:
                ctx = context(store, old)
                if not changed:
                    from .integration import install_worktree_rules
                    warnings.extend(install_worktree_rules(store, data, info, repo))
                    store.save(data)
                    return {"result": "prepared", "reused": True, "warnings": warnings, "buildContext": ctx}
            except Error as exc:
                warnings.append(str(exc))
        rid = uuid.uuid4().hex
        resource = {"id": rid, "path": str(store.root / "resources" / rid),
                    "repositoryId": info["repositoryId"], "worktree": info, "profile": profile,
                    "generation": (old["generation"] + 1) if old else 1, "createdAt": time.time(),
                    "status": "allocating", "config": config.copy(), "inputs": evidence,
                    "reason": reason or ("declared-input-changed" if changed else "fresh" if fresh else "prepare"),
                    "sharedCacheDisabled": bool(no_shared or (old and old.get("sharedCacheDisabled")
                        and old["config"].get("sharedCacheEpoch") == config.get("sharedCacheEpoch"))),
                    "sharedCache": None}
        data["resources"][rid] = resource
        store.event("allocate", resourceId=rid, worktreeId=info["worktreeId"])
        store.save(data)  # Record intent BEFORE creating any directory.
        path = no_symlinks(Path(resource["path"]))
        path.mkdir(parents=True, mode=0o700)
        resource["identity"] = identity(path)
        write_json(path / "owner.json", {"tool": "idutil", "id": rid, "repositoryId": info["repositoryId"]})
        store.save(data)  # Allocation ownership is recoverable even if later directory setup fails.
        for name in ("DerivedData", "SourcePackages", "Artifacts"):
            (path / name).mkdir(mode=0o700)
        if config.get("sharedCache") and not resource["sharedCacheDisabled"]:
            try:
                tc = toolchain(config.get("developerDir"))
                if not tc["version"].startswith("Xcode 27.0\n"):
                    raise Error("Shared compilation cache is validated only for Xcode 27.0; using private mode")
                cid = digest(json.dumps([info["repositoryId"], tc], sort_keys=True))[:24]
                cp = no_symlinks(store.root / "shared" / cid)
                if cp.exists():
                    if read_json(no_symlinks(cp / "owner.json")).get("id") != cid:
                        raise Error("Shared cache marker mismatch")
                else:
                    cp.mkdir(parents=True, mode=0o700)
                    write_json(cp / "owner.json", {"tool": "idutil", "id": cid})
                no_symlinks(cp / "CAS").mkdir(exist_ok=True, mode=0o700)
                resource["sharedCache"] = {"id": cid, "path": str(cp), "toolchain": tc}
            except (Error, OSError) as exc:
                warnings.append(str(exc))
        if old and old.get("status") != "deleted":
            old["status"] = "retired"
            old["retiredAt"] = time.time()
        resource["status"] = "active"
        data["bindings"][key] = rid
        if expected:
            data["recoveries"][recovery_key] = rid
        from .integration import install_worktree_rules
        warnings.extend(install_worktree_rules(store, data, info, repo))
        store.save(data)
        store.event("prepared", resourceId=rid, previousResourceId=oldid)
        return {"result": "prepared", "reused": False, "recovered": bool(old or fresh),
                "warnings": warnings, "buildContext": context(store, resource)}


def status(store, worktree=None, profile="default", measure=False):
    data = store.read()
    rows = []
    ctx = None
    requested = git_info(worktree) if worktree else None
    current = data["bindings"].get(key_for(requested, profile)) if requested else None
    for resource in data["resources"].values():
        if requested and resource["worktree"]["worktreeId"] != requested["worktreeId"]:
            continue
        row = {k: resource.get(k) for k in ("id", "profile", "generation", "status", "reason", "path")}
        row["worktreePath"] = resource["worktree"]["path"]
        row["presence"] = worktree_presence(resource["worktree"])
        if resource["status"] == "deleted":
            # Absence is expected after successful GC, not a broken binding.
            if measure:
                row["logicalBytes"] = 0
            rows.append(row)
            continue
        try:
            path = store.resource_path(resource)
            if measure:
                row.update(tree_measure(path))
            if resource["id"] == current:
                ctx = context(store, resource)
        except (Error, OSError) as exc:
            row["diagnostic"] = str(exc)
        rows.append(row)
    shared = {}
    for r in data["resources"].values():
        cache = r.get("sharedCache")
        if cache and r["status"] != "deleted":
            entry = shared.setdefault(cache["id"], {"path": cache["path"], "references": [], "gc": "external-native-store; excluded"})
            entry["references"].append(r["id"])
    if measure:
        for entry in shared.values():
            try:
                entry.update(tree_measure(no_symlinks(entry["path"])))
            except (Error, OSError) as exc:
                entry["diagnostic"] = str(exc)
    return {"schemaVersion": 1, "observedAt": time.time(), "resources": rows, "sharedCaches": shared,
            "buildContext": ctx, "configurationEvidence": "unknown", "usageEvidence": "unknown"}


def check_release(store, worktree):
    info = git_info(worktree)
    with store.locked() as data:
        if not data["repositories"].get(info["repositoryId"], {}).get("enabled"):
            return {"result": "not-applicable"}
        # Narrow protection: only declared paths, never a blind scan of user documents.
        repo = data["repositories"][info["repositoryId"]]
        hazards = [p for p in repo.get("protectedPaths", []) if (Path(info["path"]) / p).exists()]
        if hazards:
            raise Error("Protected worktree artifacts must be preserved before removal: " + ", ".join(hazards))
        data["pending"][info["worktreeId"]] = {"worktree": info, "checkedAt": time.time()}
        store.save(data)
        return {"result": "checked", "worktreeId": info["worktreeId"], "gc": "deferred-until-quiescent"}


def reconcile(data, path=None):
    released = []
    for key, rid in list(data["bindings"].items()):
        r = data["resources"][rid]
        if path and r["worktree"]["path"] != path:
            continue
        if path and not data["repositories"].get(r["repositoryId"], {}).get("enabled"):
            continue
        if worktree_presence(r["worktree"]) == "removed":
            r["status"] = "released"
            r["retiredAt"] = time.time()
            del data["bindings"][key]
            released.append(rid)
    return released


def release(store, path):
    # A post-remove event is advisory; current Git + filesystem facts decide.
    path = str(Path(path).expanduser().absolute())
    with store.locked() as data:
        released = reconcile(data, path)
        store.save(data)
        store.event("release", path=path, resources=released)
        return {"result": "released" if released else "deferred-or-already-released",
                "resourceIds": released, "gc": "deferred-until-quiescent"}


def busy_build_tools():
    """An additional veto, not proof of quiescence. The user attests a stopped build window."""
    out = run(["/bin/ps", "-axo", "comm="])
    names = {"Xcode", "xcodebuild", "swift-frontend", "swiftc", "clang", "clang++", "XCBBuildService", "xctest"}
    return sorted({Path(line.strip()).name for line in out.splitlines() if Path(line.strip()).name in names})


def eligible(store, data, r):
    if r["status"] == "deleted":
        return "already-deleted"
    if r["id"] in data["bindings"].values():
        return "active-binding"
    if r["status"] not in ("retired", "released", "allocating"):
        return "unknown-state"
    repo = data["repositories"].get(r["repositoryId"], {})
    age = time.time() - r.get("retiredAt", r["createdAt"])
    if age < repo.get("retentionHours", 24) * 3600:
        return "retention"
    # For retired generations in living worktrees explicit quiescence still required.
    presence = worktree_presence(r["worktree"])
    if presence == "unknown":
        return "worktree-presence-unknown"
    if r["status"] == "released" and presence != "removed":
        return "path-reused-or-reappeared"
    try:
        measured = tree_measure(store.resource_path(r))
        if measured["protected"]:
            return "protected-artifacts"
        return measured
    except (OSError, Error):
        return "ownership-or-path-unknown"


def gc(store, plan_id=None, quiescent=False):
    if plan_id and not quiescent:
        raise Error("GC apply requires --quiescent: stop build tools and keep them stopped until GC finishes")
    with store.locked() as data:
        if not plan_id:
            reconcile(data)
            items, skipped = [], []
            for r in data["resources"].values():
                result = eligible(store, data, r)
                if isinstance(result, dict):
                    items.append({"resourceId": r["id"], "identity": r["identity"], **result})
                elif result != "already-deleted":
                    skipped.append({"resourceId": r["id"], "reason": result})
            pid = uuid.uuid4().hex
            plan = {"schemaVersion": 1, "planId": pid, "createdAt": time.time(), "expiresAt": time.time() + 900,
                    "items": items, "skipped": skipped, "requiresQuiescent": True,
                    "logicalBytes": sum(x["logicalBytes"] for x in items)}
            store.save(data)
            write_json(store.root / "plans" / (pid + ".json"), plan)
            return plan
        if len(plan_id) != 32 or any(c not in "0123456789abcdef" for c in plan_id):
            raise Error("Invalid plan id")
        pp = no_symlinks(store.root / "plans" / (plan_id + ".json"))
        plan = read_json(pp)
        if plan.get("appliedAt") or time.time() > plan["expiresAt"]:
            raise Error("GC plan already consumed or expired; make a new plan")
        busy = busy_build_tools()
        if busy:
            raise Error("Build tools still running: " + ", ".join(busy))
        freed_before = shutil.disk_usage(store.root).free
        removed, skipped = [], []
        for item in plan["items"]:
            r = data["resources"].get(item["resourceId"])
            result = eligible(store, data, r) if r else "missing-record"
            if not isinstance(result, dict) or result["fingerprint"] != item["fingerprint"] or r["identity"] != item["identity"]:
                skipped.append({"resourceId": item["resourceId"], "reason": result if isinstance(result, str) else "changed-since-plan"})
                continue
            if busy_build_tools():
                skipped.append({"resourceId": r["id"], "reason": "build-tool-started"})
                break
            path = store.resource_path(r)
            # Python's fd-based rmtree refuses symlink substitution within the tree.
            if not shutil.rmtree.avoids_symlink_attacks:
                raise Error("This Python cannot safely remove a directory tree")
            store.event("gc-delete-intent", resourceId=r["id"], planId=plan_id)
            try:
                shutil.rmtree(path)
            except OSError as exc:
                skipped.append({"resourceId": r["id"], "reason": "partial-delete: " + str(exc)})
                continue
            r["status"] = "deleted"
            r["deletedAt"] = time.time()
            store.save(data)
            removed.append(r["id"])
        plan["appliedAt"] = time.time()
        write_json(pp, plan)
        result = {"planId": plan_id, "removed": removed, "skipped": skipped,
                  "freeBytesDelta": shutil.disk_usage(store.root).free - freed_before}
        store.event("gc-complete", **result)
        return result
