#!/usr/bin/env python3
"""Opt-in E2E using installed CLIs and REAL user Worktrunk/idutil configuration.

Requires wt/idutil on PATH, Xcode, xcodegen, and the sample's isolated CocoaPods
bundle. Creates a dedicated sample repository; registers only that repository.
Never closes Xcode or overrides GC's process checks. Leaves evidence and a live B
worktree for inspection. Run: python3 scripts/e2e_installed.py --install-user-hooks
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]


def main():
    args = argparse.ArgumentParser(description=__doc__)
    args.add_argument('--install-user-hooks', action='store_true', required=True,
                      help='Explicitly allow idutil setup to modify user Worktrunk hooks')
    args.parse_args()
    wt, cli = shutil.which('wt'), shutil.which('idutil')
    if not wt or not cli:
        raise SystemExit('Install wt and idutil on PATH first')
    # Ignore experiment routing variables: this test deliberately uses user defaults.
    env = dict(os.environ)
    for key in ('IDUTIL_ROOT', 'WORKTRUNK_CONFIG_PATH', 'WORKTRUNK_SYSTEM_CONFIG_PATH', 'XDG_CONFIG_HOME'):
        if env.get(key):
            raise SystemExit('Unset ' + key + ' before testing the default local installation')
    spec = importlib.util.spec_from_file_location('fixture', ROOT / 'examples/mixed-dependencies/run_experiment.py')
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    lab = fixture.LAB
    env.update(fixture.ENV)
    env['NO_COLOR'] = '1'
    fixture.ENV = env
    report = {'lab': str(lab), 'startedAt': time.strftime('%Y-%m-%dT%H:%M:%S%z'),
              'result': 'running', 'commands': [], 'checks': {},
              'limitations': ['No autonomous Claude/Codex host session or Xcode GUI test',
                              'iOS compile/link only; no simulator device created']}

    def save():
        (lab / 'installed-e2e.json').write_text(json.dumps(report, indent=2) + '\n')

    def run(label, argv, cwd=None, expect=0, timeout=240):
        start = time.monotonic()
        p = subprocess.run([str(x) for x in argv], cwd=cwd or lab, env=env,
                           capture_output=True, text=True, timeout=timeout)
        (lab / (label + '.stdout')).write_text(p.stdout)
        (lab / (label + '.stderr')).write_text(p.stderr)
        report['commands'].append({'label': label, 'argv': [str(x) for x in argv],
                                   'exitCode': p.returncode, 'seconds': time.monotonic() - start})
        save()
        if expect is not None and p.returncode != expect:
            raise RuntimeError(label + ': ' + (p.stdout + p.stderr)[-2000:])
        print(label + ': exit ' + str(p.returncode), flush=True)
        return p

    def call(label, command, cwd):
        return json.loads(run(label, [cli, *command, '--json'], cwd).stdout)

    def prepare(label, path, extra=()):
        return call(label, ['prepare', *extra], path)['buildContext']

    def build(label, ctx, path, action='build', ios=False):
        destination = 'generic/platform=iOS Simulator' if ios else 'platform=macOS,arch=arm64'
        run(label, ['/usr/bin/xcodebuild', *ctx['xcodebuildArgs'], '-configuration', 'Debug',
                    '-destination', destination, 'ARCHS=arm64', 'CODE_SIGNING_ALLOWED=NO',
                    'DEBUG_INFORMATION_FORMAT=dwarf', action], path)
        if not ios and action == 'build':
            binary = Path(ctx['derivedDataPath']) / 'Build/Products/Debug/MixedCacheProbe'
            actual = run(label + '-probe', [binary], path).stdout.strip()
            assert actual == 'remote-v1/local-v1/resource-v1/pod-v1/binary-1', actual

    try:
        report['versions'] = {name: run(name + '-version', [exe, '--version']).stdout.strip()
                              for name, exe in [('idutil', cli), ('worktrunk', wt)]}
        remote, seed = lab / 'RemoteFeature', lab / 'seed'
        shutil.copytree(fixture.HERE / 'RemoteFixture', remote)
        fixture.command(['git', 'init', '-b', 'main'], remote)
        revision = fixture.git_commit(remote, 'Remote fixture')
        shutil.copytree(fixture.HERE, seed, ignore=shutil.ignore_patterns('.bundle', 'run_experiment.py'))
        (seed / 'Vendor').mkdir()
        fixture.binary_framework(seed / 'Vendor/BinaryFeature.xcframework', 1)
        (seed / 'Tests').mkdir()
        (seed / 'Tests/CacheTests.swift').write_text('import XCTest\nimport LocalFeature\nfinal class CacheTests: XCTestCase { func testValue() { XCTAssertEqual(LocalFeature.value(), "local-v1"); XCTAssertEqual(LocalFeature.resource(), "resource-v1") } }\n')
        template = seed / 'project.template.yml'
        text = template.read_text().replace('schemes:\n', '  MixedCacheTests:\n    type: bundle.unit-test\n    platform: macOS\n    sources: [Tests]\n    settings:\n      base:\n        GENERATE_INFOPLIST_FILE: YES\n    dependencies:\n      - package: LocalFeature\nschemes:\n')
        text = text.replace('        MixedCacheProbe: all\n', '        MixedCacheProbe: all\n    test:\n      targets: [MixedCacheTests]\n')
        template.write_text(text)
        (seed / '.gitignore').write_text('Pods/\n**/xcuserdata/\n.xcodebuildmcp/\n')
        fixture.command(['git', 'init', '-b', 'main'], seed)
        fixture.configure(seed, remote, revision)
        fixture.git_commit(seed, 'Generated installed E2E fixture')
        setup = ['setup', '--ios', '--project', 'MixedCache.xcworkspace', '--scheme', 'MixedCacheProbe',
                 '--input', 'Dependencies', '--input', 'Vendor', '--agent', 'all',
                 '--developer-dir', env['DEVELOPER_DIR'], '--retention-hours', '0']
        before = Path.home() / '.config/worktrunk/config.toml'
        report['userConfigExisted'] = before.exists()
        if before.exists():
            (lab / 'worktrunk-before.toml').write_text(before.read_text())
        call('setup-preview', setup + ['--dry-run'], seed)
        installed = call('setup-installed', setup, seed)
        report['installation'] = installed
        root = Path(installed['root'])
        report['initialResourceIds'] = list(json.loads((root / 'registry.json').read_text())['resources'])
        call('setup-ios-profile', setup + ['--profile', 'ios', '--scheme', 'MixedCacheApp'], seed)
        a, b = lab / "feature a 'quoted'", lab / 'feature-b'
        contexts = {}
        for name, path in [('a', a), ('b', b)]:
            run('wt-create-' + name, [wt, '-C', seed, 'switch', '--create', name, '--base', 'main', '--path', path, '--no-cd'])
            hooked = call('hook-context-' + name, ['status', '--worktree', str(path)], seed)['buildContext']
            assert hooked and Path(hooked['derivedDataPath']).is_dir()
            assert (path / '.agents/skills/ios-disk-util/SKILL.md').is_file()
            assert (path / '.claude/skills/ios-disk-util/SKILL.md').is_file()
            fixture.configure(path, remote, revision, deployment=True)
            contexts[name] = prepare('prepare-' + name, path)
        assert contexts['a']['derivedDataPath'] != contexts['b']['derivedDataPath']
        run('wt-switch-a', [wt, '-C', seed, 'switch', 'a', '--no-cd'])
        assert prepare('prepare-a-resumed', a)['resourceId'] == contexts['a']['resourceId']
        report['checks']['hookIsolationAndReuse'] = 'passed'
        for name, path in [('a', a), ('b', b)]:
            build('build-' + name, contexts[name], path)
            settings = run('settings-' + name, ['/usr/bin/xcodebuild', *contexts[name]['xcodebuildArgs'],
                           '-configuration', 'Debug', '-showBuildSettings', '-json'], path)
            settingsfile = lab / ('settings-' + name + '.json')
            settingsfile.write_text(settings.stdout)
            diagnosis = call('doctor-' + name, ['doctor', '--integration', '--build-settings', str(settingsfile)], path)
            assert diagnosis['buildSettingsEvidence']['checkedPaths'] > 0
            assert not any(x['code'] == 'output-outside-managed-derived-data' for x in diagnosis['findings'])
            # First dependency resolution can create Package.resolved. Settle that
            # observed input once, then prove the next prepare reuses the binding.
            if any(x['code'] == 'declared-input-changed' for x in diagnosis['findings']):
                contexts[name] = prepare('prepare-resolved-' + name, path)
                build('build-resolved-' + name, contexts[name], path)
            assert prepare('reuse-after-build-' + name, path)['resourceId'] == contexts[name]['resourceId']
            assert call('doctor-settled-' + name, ['doctor', '--integration'], path)['result'] == 'ok'
        report['checks']['actualBuildPaths'] = 'passed'
        build('test-b', contexts['b'], b, 'test')
        ios = prepare('prepare-ios-b', b, ['--profile', 'ios'])
        build('build-ios-b', ios, b, ios=True)
        # Break a binding without deleting any build output; the old directory remains.
        old = contexts['a']
        dd = Path(old['derivedDataPath'])
        dd.rename(dd.with_name('DerivedData-retained-for-e2e'))
        new = prepare('recover-a', a)
        assert new['resourceId'] != old['resourceId']
        assert Path(new['derivedDataPath']).is_relative_to(root / 'resources')
        build('build-recovered-a', new, a)
        report['checks']['managedRecovery'] = 'passed'
        fixture.git_commit(a, 'Capture generated fixture resolution')
        run('wt-remove-a', [wt, '-C', seed, 'remove', '--foreground', '--no-delete-branch', 'a'])
        assert not a.exists()
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            registry = json.loads((root / 'registry.json').read_text())
            if registry['resources'][new['resourceId']]['status'] == 'released':
                break
            time.sleep(.1)
        assert registry['resources'][new['resourceId']]['status'] == 'released'
        build('build-b-after-a-removal', contexts['b'], b)
        report['checks']['removeAWhileKeepingB'] = 'passed'
        # The default store may have other projects. Never apply a plan covering them.
        plan = call('gc-plan', ['gc'], seed)
        registry = json.loads((root / 'registry.json').read_text())
        ours = {rid for rid, r in registry['resources'].items() if r['repositoryId'] == installed['repositoryId']}
        assert plan['items'], 'Expected released unprotected data to be eligible'
        assert {x['resourceId'] for x in plan['items']} <= ours, 'GC includes non-fixture resources; refusing apply'
        assert any(x['reason'] == 'active-binding' for x in plan['skipped'])
        p = run('gc-apply', [cli, 'gc', '--apply', plan['planId'], '--quiescent', '--json'], seed, expect=None)
        result = json.loads(p.stdout)
        if p.returncode == 0:
            assert result['removed']
            assert all(not (root / 'resources' / rid).exists() for rid in result['removed'])
            report['checks']['gcActualDeletion'] = 'passed'
        elif p.returncode == 2 and 'Build tools still running:' in result.get('error', ''):
            report['checks']['gcActualDeletion'] = 'blocked-by-running-build-tools'
            report['checks']['gcBusyProtection'] = 'passed'
        else:
            raise RuntimeError('Unexpected GC result: ' + p.stdout + p.stderr)
        assert Path(contexts['b']['derivedDataPath']).is_dir()
        report['contexts'] = contexts
        report['recoveredContext'] = new
        report['remainingStatus'] = call('final-status', ['status', '--measure'], seed)
        report['result'] = 'passed-with-gc-blocked' if report['checks']['gcActualDeletion'] != 'passed' else 'passed'
        save()
        print('REPORT=' + str(lab / 'installed-e2e.json'), flush=True)
    except Exception as exc:
        report['result'] = 'failed'
        report['error'] = str(exc)
        save()
        raise


if __name__ == '__main__':
    main()
