#!/usr/bin/env python3
"""Isolated mixed-dependency cache experiment. No system cleanup or configuration writes.

Requires Xcode, XcodeGen and `bundle install` for the adjacent Gemfile.
Each invocation creates its own retained lab directory. See README.md.
"""
from __future__ import annotations

import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import subprocess
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
LAB = ROOT / ".lab" / time.strftime("mixed-%Y%m%d-%H%M%S")
LAB.mkdir(parents=True, exist_ok=False)
ENV = dict(os.environ)
ENV["DEVELOPER_DIR"] = ENV.get("DEVELOPER_DIR", "/Applications/Xcode.app/Contents/Developer")
ENV["BUNDLE_GEMFILE"] = str(HERE / "Gemfile")
ENV["BUNDLE_PATH"] = str(ROOT / ".tools/bundle")
ENV["COCOAPODS_DISABLE_STATS"] = "true"
ENV["CP_HOME_DIR"] = str(LAB / "cocoapods-home")
ENV["CP_CACHE_DIR"] = str(LAB / "cocoapods-cache")
BUNDLE = ENV.get("IDUTIL_BUNDLE", "/opt/homebrew/opt/ruby@3.3/bin/bundle")
RESULTS: list[dict] = []


def command(args, cwd=LAB, timeout=300):
    result = subprocess.run([str(a) for a in args], cwd=cwd, env=ENV,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError(f"{args}\n{result.stdout[-10000:]}")
    return result.stdout.strip()


def git_commit(path, message):
    command(["git", "add", "."], path)
    command(["git", "-c", "user.name=Cache Fixture", "-c",
             "user.email=fixture@example.invalid", "commit", "-m", message], path)
    return command(["git", "rev-parse", "HEAD"], path)


def replace(path, old, new):
    content = path.read_text()
    if old not in content:
        raise RuntimeError(f"Expected {old!r} in {path}")
    path.write_text(content.replace(old, new))


def binary_framework(output, value):
    """Build real C static framework slices and package them as an XCFramework."""
    staging = LAB / f"binary-source-{value}"
    staging.mkdir()
    source = staging / "value.c"
    source.write_text(f"int BinaryFeatureValue(void) {{ return {value}; }}\n")
    frameworks = []
    for sdk, triple, platform in [
        ("macosx", "arm64-apple-macos14.0", "MacOSX"),
        ("iphonesimulator", "arm64-apple-ios17.0-simulator", "iPhoneSimulator"),
        ("iphoneos", "arm64-apple-ios17.0", "iPhoneOS"),
    ]:
        framework = staging / sdk / "BinaryFeature.framework"
        (framework / "Headers").mkdir(parents=True)
        (framework / "Modules").mkdir()
        (framework / "Headers/BinaryFeature.h").write_text("int BinaryFeatureValue(void);\n")
        (framework / "Modules/module.modulemap").write_text(
            'framework module BinaryFeature {\n  umbrella header "BinaryFeature.h"\n  export *\n}\n')
        with (framework / "Info.plist").open("wb") as f:
            plistlib.dump({"CFBundleIdentifier": "dev.iosdiskutil.BinaryFeature",
                          "CFBundleName": "BinaryFeature", "CFBundleExecutable": "BinaryFeature",
                          "CFBundlePackageType": "FMWK", "CFBundleVersion": "1",
                          "CFBundleShortVersionString": "1.0", "CFBundleSupportedPlatforms": [platform]}, f)
        sdkpath = command(["xcrun", "--sdk", sdk, "--show-sdk-path"])
        obj = staging / sdk / "value.o"
        command(["xcrun", "clang", "-target", triple, "-isysroot", sdkpath,
                 "-c", source, "-o", obj])
        command(["xcrun", "libtool", "-static", "-o", framework / "BinaryFeature", obj])
        frameworks += ["-framework", framework]
    command(["xcodebuild", "-create-xcframework", *frameworks, "-output", output])


def configure(worktree, remote, revision, deployment=False):
    template = (worktree / "project.template.yml").read_text()
    (worktree / "project.yml").write_text(template.replace("__REMOTE_URL__", remote.as_uri())
                                         .replace("__REMOTE_REVISION__", revision))
    command(["xcodegen", "generate", "--spec", "project.yml"], worktree)
    args = [BUNDLE, "exec", "pod", "install"]
    if deployment:
        args.append("--deployment")
    output = command(args, worktree)
    (LAB / f"pod-{worktree.name}-{revision[:8]}.log").write_text(output)


def fingerprint(worktree):
    inputs = {}
    for path in sorted(worktree.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        rel = path.relative_to(worktree)
        if rel.parts[0] in {"Pods", ".git", "RemoteFixture"}:
            continue
        if ".xcworkspace" in str(rel) and path.name != "Package.resolved":
            continue
        inputs[str(rel)] = hashlib.sha256(path.read_bytes()).hexdigest()
    digest = hashlib.sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest()
    return {"digest": digest, "files": inputs}


def build(name, worktree, expected=None, ios=False, cache=True, derived_label=None):
    scheme = "MixedCacheApp" if ios else "MixedCacheProbe"
    label = derived_label or (worktree.name + ("-ios" if ios else "-mac"))
    derived = LAB / "derived" / label
    cas = LAB / "cache" / "xcode27" / "CompilationCache.noindex"
    args = ["xcodebuild", "-workspace", "MixedCache.xcworkspace", "-scheme", scheme,
            "-configuration", "Debug", "-destination",
            "generic/platform=iOS Simulator" if ios else "platform=macOS,arch=arm64",
            "-derivedDataPath", derived, "-packageCachePath", LAB / "cache/packages",
            "-clonedSourcePackagesDirPath", derived / "SourcePackages",
            "-showBuildTimingSummary", "build", "CODE_SIGNING_ALLOWED=NO",
            "ARCHS=arm64", "ONLY_ACTIVE_ARCH=YES",
            "COMPILATION_CACHE_ENABLE_CACHING=" + ("YES" if cache else "NO"),
            "COMPILATION_CACHE_ENABLE_DIAGNOSTIC_REMARKS=YES",
            "COMPILATION_CACHE_CAS_PATH=" + str(cas)]
    before = fingerprint(worktree)
    start = time.monotonic()
    result = subprocess.run([str(a) for a in args], cwd=worktree, env=ENV,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, timeout=600)
    seconds = time.monotonic() - start
    (LAB / f"{name}.log").write_text(result.stdout)
    actual = None
    if result.returncode == 0 and not ios:
        actual = command([derived / "Build/Products/Debug/MixedCacheProbe"])
    record = {"name": name, "seconds": round(seconds, 3), "returncode": result.returncode,
              "platform": "iOS Simulator build only" if ios else "macOS executed",
              "expected": expected, "actual": actual,
              "passed": result.returncode == 0 and (ios or actual == expected),
              "cache_enabled": cache,
              "cache_hit_mentions": len(re.findall(r"cache hit", result.stdout, re.I)),
              "cache_miss_mentions": len(re.findall(r"cache miss", result.stdout, re.I)),
              "xcode_cache_metrics": re.findall(r"note: (\d+) hits / (\d+) cacheable tasks \(([^)]+)\)", result.stdout),
              "input_before": before, "input_after": fingerprint(worktree),
              "command": [str(a) for a in args], "log": str(LAB / f"{name}.log")}
    print(json.dumps({k: v for k, v in record.items() if k not in {"input_before", "input_after", "command"}}), flush=True)
    if not record["passed"]:
        print(result.stdout[-6000:], flush=True)
    return record


def record_build(*args, **kwargs):
    result = build(*args, **kwargs)
    RESULTS.append(result)
    save_report()
    if not result["passed"]:
        raise RuntimeError(f"Build or value assertion failed: {result['name']}")


def save_report(error=None):
    report = {"lab": str(LAB), "xcode": command(["xcodebuild", "-version"]),
              "limitations": ["Synthetic SCM dependency uses a local file:// Git remote, not an Internet registry.",
                              "CocoaPods dependency is a development pod; remote pod download caching is not tested.",
                              "iOS targets are compiled and linked but not executed on a simulator.",
                              "Runtime value assertions execute a macOS target with the same dependency sources.",
                              "No prefix mapping or remote compilation service is configured.",
                              "The fixture explicitly builds arm64 only, not x86_64.",
                              "Tiny fixture timings are not a production performance benchmark."],
              "results": RESULTS, "error": error}
    (LAB / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")


def main():
    print(f"LAB={LAB}", flush=True)
    remote = LAB / "RemoteFeature"
    shutil.copytree(HERE / "RemoteFixture", remote)
    command(["git", "init", "-b", "main"], remote)
    revision1 = git_commit(remote, "Remote fixture v1")
    replace(remote / "Sources/RemoteFeature/RemoteFeature.swift", "remote-v1", "remote-v2")
    revision2 = git_commit(remote, "Remote fixture v2")

    seed = LAB / "seed"
    shutil.copytree(HERE, seed, ignore=shutil.ignore_patterns(".bundle", "run_experiment.py"))
    (seed / "Vendor").mkdir()
    binary_framework(seed / "Vendor/BinaryFeature.xcframework", 1)
    (seed / ".gitignore").write_text("Pods/\n*.xcworkspace/\n*.xcodeproj/\nproject.yml\n")
    command(["git", "init", "-b", "main"], seed)
    git_commit(seed, "Mixed dependency fixture baseline")
    a, b = LAB / "worktree-a", LAB / "worktree-b"
    command(["git", "worktree", "add", "-b", "experiment-a", a], seed)
    command(["git", "worktree", "add", "-b", "experiment-b", b], seed)
    configure(a, remote, revision1)
    shutil.copy2(a / "Podfile.lock", b / "Podfile.lock")
    configure(b, remote, revision1, deployment=True)

    baseline = "remote-v1/local-v1/resource-v1/pod-v1/binary-1"
    record_build("01-a-cold", a, baseline)
    record_build("02-a-warm", a, baseline)
    record_build("03-b-new-path", b, baseline)
    replace(b / "Dependencies/LocalFeature/Sources/LocalFeature/LocalFeature.swift", "local-v1", "local-v2")
    value = baseline.replace("local-v1", "local-v2")
    record_build("04-b-local-source", b, value)
    replace(b / "Dependencies/LocalFeature/Sources/LocalFeature/Resources/value.txt", "resource-v1", "resource-v2")
    value = value.replace("resource-v1", "resource-v2")
    record_build("05-b-local-resource", b, value)
    replace(b / "Dependencies/PodFeature/Sources/PodFeature.m", "pod-v1", "pod-v2")
    value = value.replace("pod-v1", "pod-v2")
    record_build("06-b-pod-source", b, value)

    binary2 = LAB / "BinaryFeature-v2.xcframework"
    binary_framework(binary2, 2)
    shutil.copytree(binary2, b / "Vendor/BinaryFeature.xcframework", dirs_exist_ok=True)
    value = value.replace("binary-1", "binary-2")
    record_build("07-b-static-binary", b, value)
    configure(b, remote, revision2, deployment=True)
    value = value.replace("remote-v1", "remote-v2")
    record_build("08-b-remote-revision", b, value)

    replace(a / "Dependencies/LocalFeature/Sources/LocalFeature/LocalFeature.swift", "local-v1", "local-v3")
    replace(b / "Dependencies/LocalFeature/Sources/LocalFeature/LocalFeature.swift", "local-v2", "local-v4")
    a_value = baseline.replace("local-v1", "local-v3")
    b_value = value.replace("local-v2", "local-v4")
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(build, "09-a-concurrent", a, a_value),
                   pool.submit(build, "10-b-concurrent", b, b_value)]
        for future in futures:
            RESULTS.append(future.result())
    save_report()
    if not all(r["passed"] for r in RESULTS):
        raise RuntimeError("Concurrent value check failed")
    record_build("11-b-fresh-no-cache-control", b, b_value, cache=False, derived_label="b-control")
    record_build("12-a-ios-link", a, ios=True)
    record_build("13-b-ios-link", b, ios=True)

    # Adversarial observation: a binary can change without its path, version,
    # size, mtime or dependency lockfiles changing. Do not hide a stale result.
    binary3 = LAB / "BinaryFeature-v3.xcframework"
    binary_framework(binary3, 3)
    rel = Path("macos-arm64/BinaryFeature.framework/BinaryFeature")
    target = b / "Vendor/BinaryFeature.xcframework" / rel
    old_stat = target.stat()
    new_bytes = (binary3 / rel).read_bytes()
    if len(new_bytes) != old_stat.st_size:
        raise RuntimeError("Adversarial fixture needs equal-size binary replacement")
    target.write_bytes(new_bytes)
    os.utime(target, ns=(old_stat.st_atime_ns, old_stat.st_mtime_ns))
    expected3 = b_value.replace("binary-2", "binary-3")
    observation = build("14-b-preserved-binary-mtime", b, expected3)
    observation["kind"] = "adversarial-observation"
    observation["binary_mtime_preserved"] = target.stat().st_mtime_ns == old_stat.st_mtime_ns
    RESULTS.append(observation)
    save_report()
    record_build("15-b-fresh-content-change-control", b, expected3,
                 cache=False, derived_label="b-binary-content-control")
    content_digest = hashlib.sha256(target.read_bytes()).hexdigest()
    record_build("16-b-content-guard-shared-cache", b, expected3,
                 derived_label="b-binary-" + content_digest[:12])
    RESULTS[-1]["guard_reason"] = "Binary content changed with identical size and mtime; use fresh private build state while retaining shared compilation store."
    save_report()
    command(["git", "worktree", "list", "--porcelain"], seed)
    print(f"REPORT={LAB / 'report.json'}", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        save_report(str(error))
        raise
