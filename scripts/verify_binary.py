#!/usr/bin/env python3
"""Exercise a release executable and its durable hooks, with no idutil imports."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--binary', required=True)
    p.add_argument('--report')
    args = p.parse_args()
    original = Path(args.binary).resolve()
    wt = shutil.which('wt')
    if not wt:
        raise SystemExit('Install Worktrunk before binary verification')
    checks = []
    with tempfile.TemporaryDirectory(prefix='idutil-binary-') as temp:
        base = Path(temp).resolve()
        repo, state = base / 'repo', base / 'managed'
        binary = base / 'downloaded-idutil'
        shutil.copy2(original, binary)
        config = base / 'wt.toml'
        env = {**os.environ, 'WORKTRUNK_CONFIG_PATH': str(config),
               'WORKTRUNK_SYSTEM_CONFIG_PATH': str(base / 'no-system.toml'),
               'XDG_CONFIG_HOME': str(base / 'xdg'), 'NO_COLOR': '1'}
        def run(argv, cwd=None):
            result = subprocess.run([str(x) for x in argv], cwd=cwd or base, env=env,
                                    capture_output=True, text=True, timeout=90)
            if result.returncode:
                raise RuntimeError(str(argv) + '\n' + result.stdout + result.stderr)
            return result.stdout.strip()
        version = run([binary, '--version'])
        run(['git', 'init', '-b', 'main', repo])
        (repo / 'App.xcodeproj').mkdir()
        (repo / 'App.xcodeproj/project.pbxproj').write_text('fixture')
        run(['git', 'add', '.'], repo)
        run(['git', '-c', 'user.name=Binary Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-m', 'fixture'], repo)
        installed = json.loads(run([binary, '--root', state, 'setup', '--ios', '--worktree', repo,
                                   '--project', 'App.xcodeproj', '--scheme', 'App', '--wt-config', config,
                                   '--retention-hours', '0', '--json']))
        assert installed['warnings'] == []
        assert (repo / '.agents/skills/ios-disk-util/SKILL.md').is_file()
        checks.append('frozen setup with bundled skill')
        registry = json.loads((state / 'registry.json').read_text())
        runtime = Path(registry['repositories'][installed['repositoryId']]['runtime'])
        assert runtime.is_file() and run([runtime, '--version']) == version
        mainctx = json.loads(run([binary, '--root', state, 'prepare', '--worktree', repo, '--json']))['buildContext']
        # The installed hook must not rely on the downloaded executable or its
        # now-destroyed PyInstaller extraction directory.
        binary.unlink()
        feature = base / "feature 'with spaces'"
        run([wt, '-C', repo, 'switch', '--create', 'feature', '--base', 'main', '--path', feature, '--no-cd'])
        ctx = json.loads(run([runtime, '--root', state, 'status', '--worktree', feature, '--json']))['buildContext']
        assert ctx and ctx['derivedDataPath'] != mainctx['derivedDataPath']
        assert Path(ctx['derivedDataPath']).is_dir()
        checks.append('Worktrunk hook works after downloaded executable is removed')
        again = json.loads(run([runtime, '--root', state, 'prepare', '--worktree', feature, '--json']))['buildContext']
        assert again['resourceId'] == ctx['resourceId']
        checks.append('binding reused')
        run([wt, '-C', repo, 'remove', '--foreground', '--no-delete-branch', 'feature'])
        # Worktrunk post-remove is asynchronous even with foreground removal.
        deadline = time.monotonic() + 20
        while True:
            registry = json.loads((state / 'registry.json').read_text())
            if registry['resources'][ctx['resourceId']]['status'] == 'released' or time.monotonic() >= deadline:
                break
            time.sleep(.1)
        assert registry['resources'][ctx['resourceId']]['status'] == 'released'
        assert Path(mainctx['derivedDataPath']).is_dir()
        plan = json.loads(run([runtime, '--root', state, 'gc', '--json']))
        assert [i['resourceId'] for i in plan['items']] == [ctx['resourceId']]
        checks.append('remove hook releases only removed worktree; GC plan preserves active worktree')
    report = {'result': 'passed', 'version': version, 'checks': checks}
    if args.report:
        Path(args.report).write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
