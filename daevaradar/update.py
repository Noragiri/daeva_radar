"""Update check against the public repo's GitHub releases, and installing the latest release in place.

Release zips carry a VERSION file (written by .github/workflows/release.yml). A copy without one is either a
git checkout (only told about new versions: `git pull` updates it) or a zip from before the updater (offered
the update). Installing overwrites changed files only; settings live elsewhere (see common.SETTINGS) and files
that aren't in the release (sounds/custom, ...) are left alone."""
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import PurePosixPath

from .common import FROZEN, NO_WINDOW, ROOT, launch_command

REPO = "Noragiri/daeva_radar"
API = "https://api.github.com/repos/%s/releases/latest" % REPO
PAGE = "https://github.com/%s/releases/latest" % REPO
VERSION_FILE = ROOT / "VERSION"
PREFIX = "DaevaRadar"  # the top folder inside release zips
BUILD_SUFFIX = "-win64.zip"  # the Windows build's zip (DaevaRadar.exe); the other zip is the Python source
HEADERS = {"User-Agent": "DaevaRadar-updater", "Accept": "application/vnd.github+json"}


def current_version():
    try:
        return VERSION_FILE.read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def is_git():
    return (ROOT / ".git").exists()


def parse(version):
    """'2026.10.15-2' -> (2026, 10, 15, 2), for comparing."""
    return tuple(int(n) for n in re.findall(r"\d+", version or ""))


def latest():
    """The latest release -> {version, url, digest, commit}; raises on network or format errors."""
    req = urllib.request.Request(API, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    # the build updates from the build's zip, a Python copy from the source zip
    asset = next(a for a in data["assets"] if a["name"].endswith(".zip") and a["name"].endswith(BUILD_SUFFIX) == FROZEN)
    return dict(version=data["tag_name"].lstrip("v"), url=asset["browser_download_url"], digest=asset.get("digest"),
                commit=data.get("target_commitish"))


def is_newer(release, current):
    """No version stamp: a git checkout is behind when it lacks the release's commit; a zip from before the
    updater counts as older than any release."""
    if current is None and is_git():
        try:
            run = subprocess.run(["git", "-C", str(ROOT), "merge-base", "--is-ancestor", release["commit"], "HEAD"],
                                 capture_output=True, creationflags=NO_WINDOW, timeout=10)
        except (OSError, subprocess.SubprocessError, TypeError):
            return False  # no git: stay quiet
        return run.returncode != 0  # 0: HEAD has it; 1: it doesn't; 128: not even fetched yet
    return current is None or parse(release["version"]) > parse(current)


def install(release):
    """Download the release zip and write its changed files over this copy -> number of files changed.
    VERSION is written last, so a failed update never claims the new version."""
    if is_git():
        raise RuntimeError("This is a git checkout: update it with git pull.")
    req = urllib.request.Request(release["url"], headers={"User-Agent": HEADERS["User-Agent"]})
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = resp.read()
    digest = release.get("digest") or ""
    if digest.startswith("sha256:") and hashlib.sha256(data).hexdigest() != digest.split(":", 1)[1]:
        raise RuntimeError("The download is damaged (checksum mismatch). Nothing was changed.")
    changed = 0
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        files = []
        for info in z.infolist():
            parts = PurePosixPath(info.filename).parts
            if info.is_dir() or len(parts) < 2 or parts[0] != PREFIX or ".." in parts:
                continue
            files.append((parts[1:], info))
        if not any(parts == ("VERSION",) for parts, _ in files):
            raise RuntimeError("The release has no VERSION file. Nothing was changed.")
        for parts, info in sorted(files, key=lambda f: f[0] == ("VERSION",)):
            target = ROOT.joinpath(*parts)
            new = z.read(info)
            try:
                if target.read_bytes() == new:
                    continue
            except OSError:
                pass
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_name(target.name + ".new")
            tmp.write_bytes(new)
            try:
                os.replace(tmp, target)
            except PermissionError:  # in use (a loaded DLL on Windows): it can still be renamed out of the way
                old = target.with_name(target.name + ".old")
                old.unlink(missing_ok=True)
                target.rename(old)
                os.replace(tmp, target)
            changed += 1
    return changed


def cleanup():
    """Remove files moved aside by an earlier update (they were in use then)."""
    for old in ROOT.rglob("*.old"):
        if old.with_suffix("").exists():
            try:
                old.unlink()
            except OSError:
                pass


def restart():
    """Start a fresh copy with the same arguments (already elevated, so no new prompt); the caller closes."""
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    subprocess.Popen(launch_command() + sys.argv[1:], cwd=str(ROOT), creationflags=flags,
                     close_fds=True)
