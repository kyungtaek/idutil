#!/usr/bin/env python3
"""Opt-in real integration experiment. All generated projects/build data stay under .lab.

Requires Xcode, XcodeGen, the sample's isolated CocoaPods bundle, Worktrunk and
optionally a local XcodeBuildMCP 2.7.0 + Node. Does not touch user configuration.
Run using the development virtualenv; outputs are retained for inspection.
"""
import importlib.util
import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import threading
import time

from idutil import core, integration
from idutil.cli import parser
from idutil.storage import Store, write_json

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("fixture", ROOT / "examples/mixed-dependencies/run_experiment.py")
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)
LAB = fixture.LAB
WT = ROOT / ".tools/worktrunk/wt"
NODE = ROOT / ".tools/node/node-v22.23.4-darwin-arm64/bin/node"
MCP = ROOT / ".tools/mcp/node_modules/xcodebuildmcp/build/cli.js"
ENV = {**fixture.ENV, "PATH": str(WT.parent) + os.pathsep + str(NODE.parent) + os.pathsep + os.environ["PATH"],
       "WORKTRUNK_CONFIG_PATH": str(LAB / "wt.toml"), "WORKTRUNK_SYSTEM_CONFIG_PATH": str(LAB / "no-system.toml"),
       "XDG_CONFIG_HOME": str(LAB / "xdg"), "NO_COLOR": "1",
       "XCODEBUILDMCP_SENTRY_DISABLED": "true", "XCODEBUILDMCP_ENABLED_WORKFLOWS": "macos,session-management",
       "XCODEBUILDMCP_DISABLE_XCODE_AUTO_SYNC": "true"}
fixture.ENV = ENV
REPORT = {"lab": str(LAB), "results": [], "limitations": ["Real Claude/Codex host sessions are not executed by this script",
          "No simulator devices are created; iOS runtime tests require an available simulator"]}


def derived_snapshot():
    root = Path.home() / "Library/Developer/Xcode/DerivedData"
    return {str(p): [p.stat().st_size, p.stat().st_mtime_ns] for d in root.glob("MixedCache*")
            for p in d.rglob("*") if p.is_file()}


def command(name, argv, cwd=None, timeout=180):
    start = time.monotonic()
    p = subprocess.run([str(x) for x in argv], cwd=cwd or LAB, env=ENV, capture_output=True, text=True, timeout=timeout)
    (LAB / (name + ".log")).write_text(p.stdout + p.stderr)
    REPORT["results"].append({"name": name, "exitCode": p.returncode, "seconds": time.monotonic() - start, "argv": [str(x) for x in argv]})
    write_json(LAB / "implementation-report.json", REPORT)
    if p.returncode:
        raise RuntimeError(name + ": " + (p.stdout + p.stderr)[-2500:])
    print(name + " OK", flush=True)
    return p.stdout


class MCPClient:
    def __init__(self, cwd):
        self.err = (LAB / "mcp-stderr.log").open("w")
        self.p = subprocess.Popen([str(NODE), str(MCP), "mcp"], cwd=cwd, env=ENV,
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.err, text=True, bufsize=1)
        self.q = queue.Queue()
        self.seq = 0
        def reader():
            for line in self.p.stdout:
                try:
                    self.q.put(json.loads(line))
                except ValueError:
                    self.q.put({"noise": line})
        threading.Thread(target=reader, daemon=True).start()
        self.request("initialize", {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "idutil-verification", "version": "0.1"}})
        self.p.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
        self.p.stdin.flush()

    def request(self, method, params, timeout=180):
        self.seq += 1
        seq = self.seq
        self.p.stdin.write(json.dumps({"jsonrpc": "2.0", "id": seq, "method": method, "params": params}) + "\n")
        self.p.stdin.flush()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            msg = self.q.get(timeout=max(.1, deadline - time.monotonic()))
            if msg.get("id") == seq:
                if "error" in msg:
                    raise RuntimeError(str(msg["error"]))
                return msg["result"]
        raise TimeoutError(method)

    def call(self, name, arguments, label):
        result = self.request("tools/call", {"name": name, "arguments": arguments})
        write_json(LAB / (label + ".json"), result)
        if result.get("isError"):
            raise RuntimeError(label + ": " + json.dumps(result)[-2500:])
        REPORT["results"].append({"name": label, "tool": name, "isError": result.get("isError", False)})
        write_json(LAB / "implementation-report.json", REPORT)
        print(label + " OK", flush=True)
        return result

    def close(self):
        self.p.terminate()
        try:
            self.p.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.p.kill()
            self.p.wait()
        self.err.close()


def main():
    print("LAB=" + str(LAB), flush=True)
    remote = LAB / "RemoteFeature"
    shutil.copytree(fixture.HERE / "RemoteFixture", remote)
    fixture.command(["git", "init", "-b", "main"], remote)
    revision = fixture.git_commit(remote, "Remote fixture")
    seed = LAB / "seed"
    shutil.copytree(fixture.HERE, seed, ignore=shutil.ignore_patterns(".bundle", "run_experiment.py"))
    (seed / "Vendor").mkdir()
    fixture.binary_framework(seed / "Vendor/BinaryFeature.xcframework", 1)
    # Add real macOS XCTest coverage without requiring a simulator or UI host.
    (seed / "Tests").mkdir()
    (seed / "Tests/CacheTests.swift").write_text('import XCTest\nimport LocalFeature\nfinal class CacheTests: XCTestCase { func testValue() { XCTAssertEqual(LocalFeature.value(), "local-v1"); XCTAssertEqual(LocalFeature.resource(), "resource-v1") } }\n')
    template = seed / "project.template.yml"
    text = template.read_text().replace("schemes:\n", "  MixedCacheTests:\n    type: bundle.unit-test\n    platform: macOS\n    sources: [Tests]\n    settings:\n      base:\n        GENERATE_INFOPLIST_FILE: YES\n    dependencies:\n      - package: LocalFeature\nschemes:\n")
    text = text.replace("        MixedCacheProbe: all\n", "        MixedCacheProbe: all\n    test:\n      targets: [MixedCacheTests]\n")
    template.write_text(text)
    (seed / ".gitignore").write_text("Pods/\n**/xcuserdata/\n.xcodebuildmcp/\n")
    fixture.command(["git", "init", "-b", "main"], seed)
    fixture.configure(seed, remote, revision)
    fixture.git_commit(seed, "Generated mixed fixture")
    store = Store(LAB / "managed")
    setup = ["setup", "--ios", "--worktree", str(seed), "--project", "MixedCache.xcworkspace", "--scheme", "MixedCacheProbe",
             "--input", "Dependencies", "--input", "Vendor", "--wt-config", str(LAB / "wt.toml"),
             "--developer-dir", ENV["DEVELOPER_DIR"], "--shared-cache", "--retention-hours", "0"]
    with __import__('unittest.mock', fromlist=['patch']).patch.dict(os.environ, ENV):
        integration.setup(store, parser().parse_args(setup))
        integration.setup(store, parser().parse_args(setup + ["--profile", "ios", "--scheme", "MixedCacheApp"]))
        a, b = LAB / "worktree-a", LAB / "worktree-b"
        for label, path in [("a", a), ("b", b)]:
            command("wt-create-" + label, [WT, "-C", seed, "switch", "--create", label, "--base", "main", "--path", path, "--no-cd"])
            fixture.configure(path, remote, revision, deployment=True)
        contexts = {"a": core.prepare(store, a)["buildContext"], "b": core.prepare(store, b)["buildContext"]}
        write_json(LAB / "contexts.json", contexts)
        baseline = "remote-v1/local-v1/resource-v1/pod-v1/binary-1"
        for label, path in [("a", a), ("b", b)]:
            ctx = contexts[label]
            args = ["/usr/bin/xcodebuild", *ctx["xcodebuildArgs"], "-configuration", "Debug", "-destination", "platform=macOS,arch=arm64", "CODE_SIGNING_ALLOWED=NO", "ARCHS=arm64", "build"]
            command("xcodebuild-" + label, args, path)
            exe = Path(ctx["derivedDataPath"]) / "Build/Products/Debug/MixedCacheProbe"
            actual = command("probe-" + label, [exe], path).strip()
            assert actual == baseline, actual
            settings = command("settings-" + label, ["/usr/bin/xcodebuild", *ctx["xcodebuildArgs"], "-configuration", "Debug", "-showBuildSettings", "-json"], path)
            (LAB / ("settings-" + label + ".json")).write_text(settings)
        ctx = contexts["a"]
        command("xcodebuild-tests", ["/usr/bin/xcodebuild", *ctx["xcodebuildArgs"], "-destination", "platform=macOS,arch=arm64", "-configuration", "Debug", "test", "CODE_SIGNING_ALLOWED=NO"], a)
        ios = core.prepare(store, b, profile="ios")["buildContext"]
        command("xcodebuild-ios-link", ["/usr/bin/xcodebuild", *ios["xcodebuildArgs"], "-destination", "generic/platform=iOS Simulator", "ARCHS=arm64", "CODE_SIGNING_ALLOWED=NO", "build"], b)
        if NODE.is_file() and MCP.is_file():
            client = MCPClient(a)
            try:
                tools = client.request("tools/list", {})
                write_json(LAB / "mcp-tool-schemas.json", tools)
                for label in ("a", "b", "a"):
                    ctx = contexts[label]
                    defaults = {**ctx["mcpSessionDefaults"], "configuration": "Debug", "arch": "arm64", "preferXcodebuild": True}
                    defaults["extraArgs"] = defaults["extraArgs"] + ["CODE_SIGNING_ALLOWED=NO", "ARCHS=arm64"]
                    client.call("session_set_defaults", defaults, "mcp-set-" + label)
                    shown = client.call("session_show_defaults", {}, "mcp-show-" + label)
                    assert ctx["derivedDataPath"] in json.dumps(shown)
                    client.call("build_macos", {}, "mcp-build-" + label)
                    exe = Path(ctx["derivedDataPath"]) / "Build/Products/Debug/MixedCacheProbe"
                    assert command("mcp-probe-" + label, [exe], a if label == "a" else b).strip() == baseline
                before = derived_snapshot()
                client.call("test_macos", {}, "mcp-test-a")
                after = derived_snapshot()
                leaks = [p for p, s in after.items() if before.get(p) != s]
                REPORT["mcpTestIsolation"] = {"passed": not leaks, "unexpectedDefaultWrites": leaks}
                if leaks:
                    REPORT["limitations"].append("XcodeBuildMCP 2.7.0 test_macos writes default DerivedData metadata; use xcodebuild test")
            finally:
                client.close()
        old = contexts["b"]
        new = core.prepare(store, b, fresh=True, expected=old["resourceId"], reason="injected-cache-error", no_shared=True)["buildContext"]
        assert new["resourceId"] != old["resourceId"] and new["cacheMode"] == "private-only"
        command("xcodebuild-recovered-b", ["/usr/bin/xcodebuild", *new["xcodebuildArgs"], "-destination", "platform=macOS,arch=arm64", "CODE_SIGNING_ALLOWED=NO", "build"], b)
        assert command("recovered-probe-b", [Path(new["derivedDataPath"]) / "Build/Products/Debug/MixedCacheProbe"], b).strip() == baseline
        REPORT["result"] = "passed-with-adapter-limitations" if REPORT.get("mcpTestIsolation", {}).get("passed") is False else "passed"
        REPORT["contexts"] = contexts
        REPORT["recoveredContext"] = new
        REPORT["logicalBytes"] = core.status(store, measure=True)
        write_json(LAB / "implementation-report.json", REPORT)
        print("REPORT=" + str(LAB / "implementation-report.json"), flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        REPORT["result"] = "failed"
        REPORT["error"] = str(exc)
        write_json(LAB / "implementation-report.json", REPORT)
        raise
