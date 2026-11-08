"""Build both assistant downloads from the same versioned marketplace.

Codex asset names and installers remain compatible with published setup commands.
The Claude Code ZIP is an identical package with its own discoverable asset name.
The DeepSeek Harness bundle ships twice with identical bytes: versioned, and as
dsh-gamma.tgz, the stable name Gamma's setup command installs from /releases/latest.
"""

import argparse
import hashlib
import json
import re
import shutil
from pathlib import Path

from package_plugins import MANIFEST_PATHS, build, validate_repo, write_archive, write_dsh_bundle

REPO = "tim4431/Gamma"


def release_codex(output: Path, version: str, repo: str = REPO) -> list[Path]:
    """The Codex ZIP, its two installers pinned to it, and their checksums."""
    if not re.fullmatch(r"\d+\.\d+\.\d+(?:-[A-Za-z0-9.-]+)?", version):
        raise ValueError("Release version must be X.Y.Z, optionally with a prerelease suffix.")
    validate_repo(repo)
    package = build(output / "gamma-marketplace")
    for relative in MANIFEST_PATHS:
        manifest_path = package / "plugins/gamma" / relative
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["version"] = version
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    archive = output / f"gamma-codex-plugin-{version}.zip"
    write_archive(package, archive)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    assets = [archive]
    for name in ("install-gamma-codex.ps1", "install-gamma-codex.sh"):
        script = Path(__file__).with_name(name).read_text(encoding="utf-8")
        script = script.replace("__GAMMA_ARCHIVE_URL__", f"https://github.com/{repo}/releases/download/v{version}/{archive.name}")
        script = script.replace("__GAMMA_ARCHIVE_SHA256__", digest)
        dest = output / name
        dest.write_text(script, encoding="utf-8", newline="\n")
        assets.append(dest)
    checksums = output / "gamma-codex-SHA256SUMS.txt"
    checksums.write_text("".join(f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}\n" for p in assets), encoding="utf-8")
    return [*assets, checksums]


def release(output: Path, version: str, repo: str = REPO) -> list[Path]:
    # Never overwrite an existing artifact, including a partial previous release.
    if list(output.glob("gamma-claude-code-*")) or list(output.glob("*dsh*")):
        raise ValueError("Claude Code or dsh release output already exists. Choose a new directory.")
    assets = release_codex(output, version, repo)
    archive = output / f"gamma-claude-code-plugin-{version}.zip"
    checksums = output / "gamma-claude-code-SHA256SUMS.txt"
    shutil.copy2(assets[0], archive)
    checksums.write_text(f"{hashlib.sha256(archive.read_bytes()).hexdigest()}  {archive.name}\n",
                         encoding="utf-8")
    dsh = write_dsh_bundle(output / f"dsh-gamma-{version}.tgz", version)
    dsh_latest = output / "dsh-gamma.tgz"
    shutil.copy2(dsh, dsh_latest)
    dsh_checksums = output / "gamma-dsh-SHA256SUMS.txt"
    dsh_checksums.write_text("".join(f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}\n"
                                     for p in (dsh, dsh_latest)), encoding="utf-8")
    return [*assets, archive, checksums, dsh, dsh_latest, dsh_checksums]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--repo", default=REPO)
    args = parser.parse_args()
    try:
        for asset in release(args.output, args.version, args.repo):
            print(asset)
    except ValueError as exc:
        parser.error(str(exc))
