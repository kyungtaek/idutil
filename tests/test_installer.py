"""Release installer integrity and atomic replacement tests, without networking."""
import hashlib
import io
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.fake = self.root / 'commands'
        self.fake.mkdir()
        self.assets = self.root / 'assets'
        self.assets.mkdir()
        self.target = self.root / 'install with spaces'
        self.target.mkdir()
        self.script = Path(__file__).resolve().parents[1] / 'scripts/install.sh'
        self.env = {**os.environ, 'PATH': str(self.fake) + os.pathsep + os.environ['PATH'],
                    'FIXTURE_ASSETS': str(self.assets), 'IDUTIL_VERSION': 'v0.2.0',
                    'IDUTIL_BIN_DIR': str(self.target)}
        for name, body in [('uname', 'case "$1" in -s) echo "${FIXTURE_OS:-Darwin}";; *) echo arm64;; esac\n'),
                           ('sw_vers', 'echo 15.0\n')]:
            self.command(name, '#!/bin/sh\n' + body)
        self.command('curl', '#!' + sys.executable + '\n' +
                     'import os,sys,shutil\nfrom pathlib import Path\n'
                     'a=sys.argv[1:]\nurl=next(x for x in a if x.startswith("https://"))\n'
                     'out=a[a.index("-o")+1]\n'
                     'shutil.copyfile(Path(os.environ["FIXTURE_ASSETS"])/url.rsplit("/",1)[-1],out)\n')
        self.make_archive()

    def tearDown(self):
        self.tmp.cleanup()

    def command(self, name, text):
        p = self.fake / name
        p.write_text(text)
        p.chmod(0o755)

    def make_archive(self, name='idutil', executable_version='0.2.0'):
        asset = self.assets / 'idutil-v0.2.0-macos-arm64.tar.gz'
        payload = ('#!/bin/sh\necho "idutil ' + executable_version + '"\n').encode()
        with tarfile.open(asset, 'w:gz') as tar:
            entry = tarfile.TarInfo(name)
            entry.size = len(payload)
            entry.mode = 0o755
            tar.addfile(entry, io.BytesIO(payload))
        (self.assets / 'SHA256SUMS').write_text(hashlib.sha256(asset.read_bytes()).hexdigest() + '  ' + asset.name + '\n')

    def install(self, **env):
        return subprocess.run(['/bin/sh', str(self.script)], env={**self.env, **env}, capture_output=True, text=True)

    def test_valid_archive_installs_and_updates(self):
        self.assertEqual(self.install().returncode, 0)
        self.assertEqual(subprocess.check_output([self.target / 'idutil', '--version'], text=True).strip(), 'idutil 0.2.0')
        self.assertEqual(self.install().returncode, 0)

    def test_bad_checksum_preserves_existing_executable(self):
        p = self.target / 'idutil'
        p.write_text('original')
        (self.assets / 'SHA256SUMS').write_text('0' * 64 + '  idutil-v0.2.0-macos-arm64.tar.gz\n')
        self.assertNotEqual(self.install().returncode, 0)
        self.assertEqual(p.read_text(), 'original')

    def test_wrong_version_or_archive_member_refused(self):
        self.make_archive(executable_version='9.0.0')
        self.assertNotEqual(self.install().returncode, 0)
        self.assertFalse((self.target / 'idutil').exists())
        self.make_archive(name='../escape')
        self.assertNotEqual(self.install().returncode, 0)
        self.assertFalse((self.root / 'escape').exists())

    def test_package_manager_symlink_preserved(self):
        original = self.root / 'original'
        original.write_text('keep')
        (self.target / 'idutil').symlink_to(original)
        self.assertNotEqual(self.install().returncode, 0)
        self.assertEqual(original.read_text(), 'keep')
        self.assertTrue((self.target / 'idutil').is_symlink())

    def test_unsupported_os_and_invalid_version_refused(self):
        self.assertNotEqual(self.install(FIXTURE_OS='Linux').returncode, 0)
        self.assertNotEqual(self.install(IDUTIL_VERSION='v../../escape').returncode, 0)


if __name__ == '__main__':
    unittest.main()
