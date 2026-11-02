idutil 0.2.1 adds an interactive remote installer for macOS.

- Select a release version and an absolute installation directory; Enter keeps the displayed defaults.
- Confirm the download, view download progress, then confirm installation or replacement after integrity verification. Enter or no cancels.
- Answer through the terminal when running `curl … | sh`.
- Use `--yes` / `-y` for unattended installs; checksum, archive and version checks remain mandatory.
- Existing executable files remain intact when verification fails or replacement is declined. Package-manager symlinks remain protected.

Install on macOS 15 or later:

```sh
installer="$(mktemp)"
curl --proto '=https' --tlsv1.2 -fsSL https://github.com/kyungtaek/idutil/releases/latest/download/install.sh -o "$installer" && sh "$installer"
rm -f "$installer"
```

Install Worktrunk separately (`brew install worktrunk`), put `~/.local/bin` on PATH, then run `idutil setup --ios --project App.xcworkspace --scheme App --agent all` in your iOS Git repository. Update existing registered repositories with `idutil setup --update`.

For an existing uv installation, first run `uv tool uninstall ios-disk-util`; the installer deliberately preserves package-manager symlinks.

Checks: source tests and real Worktrunk integration; per-architecture standalone-binary smoke tests, including hooks after removal of the downloaded executable. Earlier mixed-dependency Xcode/GC E2E evidence is retained in the repository. XcodeBuildMCP 2.7.0 test tools still drop the DerivedData argument in one stage: use existing `xcodebuild test` with the managed context. Xcode GUI and autonomous agent sessions are not verified. Binaries are ad-hoc signed, not Developer ID signed/notarized. This is an early release; idutil never runs builds or manages worktrees itself.
