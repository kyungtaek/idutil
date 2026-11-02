Standalone macOS distribution of idutil, the Worktrunk companion for isolated DerivedData and conservative GC.

- Apple Silicon (arm64) and Intel (x86_64) executables with an embedded Python runtime. No Python or uv installation required.
- Download installer with architecture selection, SHA-256 verification, executable version checking, and atomic replacement.
- Durable Worktrunk hook runtime survives removal or replacement of the downloaded executable.
- Worktree-specific DerivedData/SourcePackages, managed recovery, status/doctor, protected-artifact-aware GC, and Codex/Claude project rules.
- Successful GC resources now show zero bytes instead of an incorrect missing-path diagnostic.

Install on macOS 15 or later:

```sh
installer="$(mktemp)"
curl --proto '=https' --tlsv1.2 -fsSL https://github.com/kyungtaek/idutil/releases/latest/download/install.sh -o "$installer" && sh "$installer"
rm -f "$installer"
```

Install Worktrunk separately (`brew install worktrunk`), put `~/.local/bin` on PATH, then run `idutil setup --ios --project App.xcworkspace --scheme App --agent all` in your iOS Git repository. Update existing registered repositories with `idutil setup --update`.

For an existing uv installation, first run `uv tool uninstall ios-disk-util`; the installer deliberately preserves package-manager symlinks.

Checks: source tests and real Worktrunk integration; per-architecture standalone-binary smoke tests, including hooks after removal of the downloaded executable. Earlier mixed-dependency Xcode/GC E2E evidence is retained in the repository. XcodeBuildMCP 2.7.0 test tools still drop the DerivedData argument in one stage: use existing `xcodebuild test` with the managed context. Xcode GUI and autonomous agent sessions are not verified. Binaries are ad-hoc signed, not Developer ID signed/notarized. This is an early release; idutil never runs builds or manages worktrees itself.
