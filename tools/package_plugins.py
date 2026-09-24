"""Build a credential-free marketplace for Codex and Claude Code, and the
DeepSeek Harness bundle (an npm tarball) from the same plugin directory.

Never edits either assistant's user configuration or an existing marketplace.
Run from any directory: python tools/package_plugins.py --output <new-dir>
"""

import argparse
import gzip
import io
import json
import re
import shutil
import tarfile
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


MANIFEST_PATHS = (".codex-plugin/plugin.json", ".claude-plugin/plugin.json")
PLUGIN_FILES = (*MANIFEST_PATHS, "README.md", "skills/gamma/SKILL.md",
                "skills/gamma/agents/openai.yaml", "skills/gamma/assets/icon.png")
# The dsh bundle: package.json declares dsh.bundle; its "files" must list the rest.
DSH_FILES = ("package.json", "README.md", "cordis.patch.yml", "dsh-skill.js", "skills/gamma/SKILL.md")
SOURCE = Path(__file__).resolve().parents[1] / "plugins" / "gamma"


def load_manifests(source: Path = SOURCE) -> list[dict]:
    """Both native manifests and the dsh package.json, checked for drift."""
    manifests = [json.loads((source / path).read_text(encoding="utf-8")) for path in MANIFEST_PATHS]
    for manifest in manifests:
        if manifest.get("name") != "gamma" or manifest.get("mcpServers") or manifest.get("apps"):
            raise ValueError("Expected the credential-free Gamma skills plugin.")
    # Native manifests stay loadable from a checkout; keep shared metadata in sync.
    for key in ("name", "version", "description", "author", "homepage", "repository", "skills"):
        if manifests[0].get(key) != manifests[1].get(key):
            raise ValueError(f"Plugin manifests disagree on {key}.")
    package = json.loads((source / "package.json").read_text(encoding="utf-8"))
    for key in ("version", "description", "homepage", "repository"):
        if package.get(key) != manifests[1].get(key):
            raise ValueError(f"dsh package.json disagrees with the plugin manifests on {key}.")
    if sorted(package.get("files", [])) != sorted(f for f in DSH_FILES if f not in ("package.json", "README.md")):
        raise ValueError("dsh package.json files must match the bundle allowlist.")
    return [*manifests, package]


def write_dsh_bundle(destination: Path, version: str | None = None) -> Path:
    """An npm-style tarball (package/ prefix) that `dsh plugin add` installs.

    Byte-reproducible: fixed timestamps and owners, sorted entries.
    """
    package = load_manifests()[2]
    if version:
        package["version"] = version
    with open(destination, "xb") as out:
        with gzip.GzipFile(fileobj=out, mode="wb", mtime=0) as gz, tarfile.open(fileobj=gz, mode="w") as tar:
            for relative in sorted(DSH_FILES):
                if relative == "package.json":
                    data = (json.dumps(package, indent=2) + "\n").encode()
                else:
                    data = (SOURCE / relative).read_bytes()
                info = tarfile.TarInfo(f"package/{relative}")
                info.size, info.mode, info.mtime = len(data), 0o644, 0
                tar.addfile(info, io.BytesIO(data))
    return destination


def write_archive(package: Path, destination: Path) -> None:
    """Include hidden catalog files and keep one top-level package directory."""
    with ZipFile(destination, "x", ZIP_DEFLATED) as bundle:
        for file in sorted(package.rglob("*")):
            if file.is_file():
                bundle.write(file, (Path(package.name) / file.relative_to(package)).as_posix())


def validate_repo(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", value):
        raise ValueError("GitHub repository must be owner/repo.")
    return value


def build(target: Path, github_repo: str | None = None, archive: bool = False):
    """Export only distributable plugin files, never local connections or caches."""
    target = target.resolve()
    archive_path = target.with_name(target.name + ".zip")
    if target.exists() or (archive and archive_path.exists()):
        raise ValueError("Output already exists. Choose a new directory to preserve the existing package.")
    if github_repo:
        validate_repo(github_repo)
    source = SOURCE
    manifests = load_manifests(source)
    # Explicit allowlist: a developer's .env, caches or MCP config cannot leak.
    for relative in PLUGIN_FILES:
        dest = target / "plugins" / "gamma" / relative
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / relative, dest)
    docs = target / "docs" / "dev"
    docs.mkdir(parents=True)
    shutil.copy2(source.parents[1] / "docs" / "dev" / "mcp.md", docs / "mcp.md")
    shutil.copy2(source.parents[1] / "PRIVACY.md", target / "PRIVACY.md")
    catalog = target / ".agents" / "plugins" / "marketplace.json"
    catalog.parent.mkdir(parents=True)
    catalog.write_text(json.dumps({
        "name": "gamma-local",
        "interface": {"displayName": "Gamma PDF"},
        "plugins": [{"name": "gamma", "source": {"source": "local", "path": "./plugins/gamma"},
                     "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"},
                     "category": "Productivity"}],
    }, indent=2) + "\n", encoding="utf-8")
    claude_catalog = target / ".claude-plugin" / "marketplace.json"
    claude_catalog.parent.mkdir(parents=True)
    claude_catalog.write_text(json.dumps({
        "name": "gamma-local",
        "owner": {"name": "Gamma"},
        "metadata": {"description": "Search and read Gamma PDF libraries with Claude Code."},
        "plugins": [{"name": "gamma", "source": "./plugins/gamma",
                     "description": manifests[1]["description"], "category": "productivity"}],
    }, indent=2) + "\n", encoding="utf-8")
    install_source = github_repo or f'"./{target.name}"'
    template = Path(__file__).with_name("marketplace-README.md").read_text(encoding="utf-8")
    install_intro = ("After this repository is published, run:" if github_repo else
                     "Extract the archive and run this from the parent of the extracted directory:")
    (target / "README.md").write_text(template.format(install_intro=install_intro, install_source=install_source),
                                    encoding="utf-8")
    if archive:
        write_archive(target, archive_path)
    return target


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--github-repo", help="Destination marketplace repository, owner/repo; only used in installation instructions")
    parser.add_argument("--archive", action="store_true", help="Also create a ZIP beside the output directory")
    args = parser.parse_args()
    try:
        target = build(args.output, args.github_repo, args.archive)
    except ValueError as exc:
        parser.error(str(exc))
    print(f"Marketplace written to {target}")
    print("Keep this directory as the marketplace source after installation.")
    print(f'Codex: codex plugin marketplace add "{target}"')
    print(f'Claude Code: claude plugin marketplace add "{target}"')
    print("For Claude Code, run: claude plugin install gamma@gamma-local --scope user")
    print("For Codex, install Gamma PDF in the plugin browser.")
    print("Configure your Gamma MCP connection separately, then start a new conversation.")


if __name__ == "__main__":
    main()
