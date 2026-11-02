---
name: ios-disk-util
description: Use registered iOS worktrees with Worktrunk and idutil-managed DerivedData, including xcodebuild/XcodeBuildMCP path configuration and cache recovery. Applies only to repositories explicitly registered with idutil.
---

# iOS build spaces

Use Worktrunk (`wt`) for worktree creation, lookup, switching and removal.
Use existing Xcode tools to build, test and run. idutil only manages data.

Before the first build/test, after switching/resuming, and when context is missing:

1. Run `@IDUTIL@ prepare --worktree <absolute-worktree-path> --json`.
2. Use its current `buildContext`. For xcodebuild, pass the returned `xcodebuildArgs`
   and `environment`, adding the requested action and destination. Do not execute
   JSON as shell code: pass arguments as an array or quote each argument.
3. For XcodeBuildMCP, inspect the installed schema. Apply `mcpSessionDefaults`
   through `session_set_defaults`, clear the obsolete project/workspace key named
   by `clearMCPDefault`, then verify with `session_show_defaults`. Respect existing
   unrelated settings. Check per-call overrides and extraArgs for conflicts.
   Never assume writing a config file updates an already running MCP server.
   XcodeBuildMCP 2.7.0's test pipeline drops derivedDataPath in its prepared-test
   execution step. For that version use existing `xcodebuild test` with this
   context instead of MCP test tools. Its macOS build path is verified; other
   versions/actions require verification. MCP-owned logs/results remain external.
4. Before each build/test invocation, compare the effective project/workspace,
   derivedDataPath and output overrides to this binding. A normal switch or MCP
   restart reuses valid managed data; restore its settings instead of making a new folder.

Use separate MCP sessions or verified explicit per-call paths for concurrent
worktrees. An incompatible/unavailable tool option is a configuration problem;
do not silently use the default DerivedData. Xcode GUI needs separate setup.

For a missing/corrupt binding or concrete cache error, run once:

`@IDUTIL@ prepare --worktree <path> --fresh --expected-resource <failed-resourceId> --reason <cache-error> --json`

Omit `--expected-resource` only if no prior binding exists. If the shared compiler
cache itself is suspect, add `--no-shared-cache`. Apply the returned new context
and continue using the existing build tool. For test-without-building or running
an app, rebuild the required products first. Do not rotate for ordinary source,
signing, dependency-download or test failures. Do not create unmanaged temp paths.
The previous generation is preserved for later GC; do not delete it manually.

`status --worktree <path> --json` is read-only. `doctor` diagnoses without building.
Preparation success does not prove tool configuration or actual usage. Report
the real command/tool settings and outputs, keeping unobserved facts unknown.

Use `gc` to preview candidates. Applying a plan requires an explicitly stopped
build window (`--quiescent`) with related tools kept stopped until completion.
Never attest quiescence because an agent is idle. Protected archives, dSYMs,
xcresults and `.idutil-keep` are retained. This skill does not authorize arbitrary
user-data cleanup or override the host's trust/approval policy.
