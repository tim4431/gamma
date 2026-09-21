"""Use Gamma's existing brand icon; sips is included with macOS."""
import json
import subprocess
from pathlib import Path

root = Path(__file__).resolve().parents[2]
catalog = root / "ipad/GammaIPad/Assets.xcassets"
icons = catalog / "AppIcon.appiconset"
icons.mkdir(parents=True, exist_ok=True)
subprocess.run(["sips", "-z", "1024", "1024", str(root / "frontend/public/media/icons/icon-maskable-512.png"),
                "--out", str(icons / "icon.png")], check=True)
(catalog / "Contents.json").write_text(json.dumps({"info": {"author": "xcode", "version": 1}}))
(icons / "Contents.json").write_text(json.dumps({"images": [
    {"filename": "icon.png", "idiom": "universal", "platform": "ios", "size": "1024x1024"}
], "info": {"author": "xcode", "version": 1}}))
