"""Atomic registry and owned filesystem boundaries. Never infer ownership from a name."""
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import time
import uuid


class Error(Exception):
    pass


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def identity(path):
    s = Path(path).stat()
    return [s.st_dev, s.st_ino, getattr(s, "st_birthtime", s.st_ctime)]


def no_symlinks(path):
    path = Path(os.path.abspath(os.path.expanduser(str(path))))
    for component in (path, *path.parents):
        if component.is_symlink():
            raise Error("Symlink in managed path: " + str(component))
    return path


def read_json(path):
    try:
        return json.loads(Path(path).read_text())
    except (ValueError, OSError) as exc:
        raise Error("Cannot read JSON {}: {}".format(path, exc))


def atomic_write(path, data, mode=0o600):
    path = no_symlinks(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_name("." + path.name + "." + uuid.uuid4().hex)
    try:
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
        with os.fdopen(fd, "w") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(str(tmp), str(path))
        directory = os.open(str(path.parent), os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if tmp.exists():
            tmp.unlink()


def write_json(path, data):
    atomic_write(path, json.dumps(data, indent=2, sort_keys=True) + "\n")


def run(args, cwd=None, env=None, timeout=15):
    try:
        p = subprocess.run(args, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise Error("Cannot run {}: {}".format(args[0], exc))
    if p.returncode:
        raise Error("{} failed: {}".format(args[0], p.stderr.strip()[-1500:]))
    return p.stdout.strip()


def git_info(path):
    path = Path(path).expanduser().resolve(strict=True)
    top = Path(run(["git", "-C", str(path), "rev-parse", "--show-toplevel"])).resolve()
    common = Path(run(["git", "-C", str(top), "rev-parse", "--path-format=absolute", "--git-common-dir"])).resolve()
    gitdir = Path(run(["git", "-C", str(top), "rev-parse", "--absolute-git-dir"])).resolve()
    info = {"path": str(top), "common": str(common), "gitDir": str(gitdir),
            "pathIdentity": identity(top), "gitIdentity": identity(gitdir),
            "commonIdentity": identity(common)}
    info["repositoryId"] = digest(json.dumps([str(common), info["commonIdentity"]]))[:24]
    info["worktreeId"] = digest(json.dumps([info["repositoryId"], str(top), info["pathIdentity"],
                                            str(gitdir), info["gitIdentity"]]))[:24]
    return info


def worktree_presence(info):
    """Only two independently verified absences establish removal. Unknown protects."""
    try:
        if identity(info["common"]) != info["commonIdentity"]:
            return "unknown"
        raw = run(["git", "--git-dir", info["common"], "worktree", "list", "--porcelain", "-z"])
        listed = [x[len("worktree "):] for x in raw.split("\0") if x.startswith("worktree ")]
        target = Path(info["path"])
        if target.exists() or target.is_symlink() or info["path"] in listed:
            return "present"
        # An inaccessible/missing parent or different mounted volume is not absence evidence.
        if not target.parent.is_dir() or target.parent.stat().st_dev != info["pathIdentity"][0]:
            return "unknown"
        return "removed"
    except (Error, OSError):
        return "unknown"


class Store:
    def __init__(self, root):
        self.root = no_symlinks(root)
        self.registry = self.root / "registry.json"

    def read(self):
        if not self.registry.exists():
            return {"schemaVersion": 1, "repositories": {}, "bindings": {}, "resources": {},
                    "pending": {}, "recoveries": {}, "installations": {}}
        no_symlinks(self.registry)
        data = read_json(self.registry)
        if data.get("schemaVersion") != 1:
            raise Error("Unsupported registry schema; refusing migration or reset")
        for key in ("repositories", "bindings", "resources", "pending", "recoveries", "installations"):
            if not isinstance(data.get(key), dict):
                raise Error("Registry is incomplete: " + key)
        return data

    def initialize(self):
        if self.root.exists():
            if not (self.root / "owner.json").exists():
                if any(self.root.iterdir()):
                    raise Error("Management root must be empty or already owned by idutil")
            if self.root.stat().st_uid != os.getuid():
                raise Error("Management root belongs to another user")
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        owner = self.root / "owner.json"
        if not owner.exists():
            write_json(owner, {"tool": "idutil", "schemaVersion": 1, "id": uuid.uuid4().hex})
        if read_json(owner).get("tool") != "idutil":
            raise Error("Management root ownership mismatch")

    @contextlib.contextmanager
    def locked(self):
        self.initialize()
        p = no_symlinks(self.root / "registry.lock")
        fd = os.open(str(p), os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            start = time.monotonic()
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() - start > 10:
                        raise Error("Registry is busy; no data was deleted")
                    time.sleep(.05)
            yield self.read()
        finally:
            os.close(fd)

    def save(self, data):
        write_json(self.registry, data)

    def event(self, operation, **fields):
        event = {"time": time.time(), "operation": operation, **fields}
        write_json(self.root / "journal" / (uuid.uuid4().hex + ".json"), event)

    def resource_path(self, resource):
        rid = resource.get("id", "")
        if len(rid) != 32 or any(c not in "0123456789abcdef" for c in rid):
            raise Error("Invalid resource identifier")
        expected = self.root / "resources" / resource["id"]
        if resource["path"] != str(expected):
            raise Error("Resource path mismatch")
        no_symlinks(expected)
        if not expected.is_dir() or identity(expected) != resource.get("identity"):
            raise Error("Resource identity changed or missing")
        marker = read_json(no_symlinks(expected / "owner.json"))
        if marker != {"tool": "idutil", "id": resource["id"], "repositoryId": resource["repositoryId"]}:
            raise Error("Resource owner marker mismatch")
        return expected


def tree_measure(path):
    """Does not follow symlinks. A digest detects plan-to-apply changes, not build quiescence."""
    size = 0
    fingerprint = hashlib.sha256()
    protected = []
    for root, dirs, files in os.walk(path, followlinks=False):
        for name in sorted(dirs + files):
            p = Path(root) / name
            s = p.lstat()
            rel = str(p.relative_to(path))
            fingerprint.update(json.dumps([rel, s.st_ino, s.st_size, s.st_mtime_ns, s.st_mode]).encode())
            if stat.S_ISREG(s.st_mode):
                size += s.st_size
            if stat.S_ISLNK(s.st_mode):
                fingerprint.update(os.readlink(str(p)).encode())
            if name.endswith((".xcarchive", ".dSYM", ".xcresult")) or name == ".idutil-keep":
                protected.append(rel)
    return {"logicalBytes": size, "fingerprint": fingerprint.hexdigest(), "protected": protected}
