"""Build two independent distributions with byte-identical reduction engines."""
from pathlib import Path
import re
import shutil
import zipfile

ROOT = Path(__file__).resolve().parent
ADDON = ROOT / "addon" / "clean_quad_reduce"
SKILL = ROOT / "skill" / "blender-clean-quad-reduce"
DIST = ROOT / "dist"


def archive(path, source, prefix=""):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for item in sorted(source.rglob("*")):
            if item.is_file() and "__pycache__" not in item.parts and not item.name.endswith(".pyc"):
                z.write(item, str(Path(prefix) / item.relative_to(source)))


def main():
    DIST.mkdir(exist_ok=True)
    shutil.copyfile(ADDON / "core.py", SKILL / "scripts" / "clean_quad_core.py")
    shutil.copyfile(ADDON / "aligned.py", SKILL / "scripts" / "clean_quad_aligned.py")
    manifest = (ADDON / "blender_manifest.toml").read_text(encoding="utf-8")
    addon_version = re.search(r'^version = "([^"\n]+)"$', manifest, re.MULTILINE).group(1)
    archive(DIST / f"clean_quad_reduce-{addon_version}.zip", ADDON)
    archive(DIST / "blender-clean-quad-reduce-1.0.0.zip", SKILL, SKILL.name)
    for name in ("README.md", "LICENSE"):
        if (ROOT / name).exists():
            shutil.copyfile(ROOT / name, DIST / ("使い方.md" if name == "README.md" else name))
    print("Built standalone Blender extension and standalone Codex skill:")
    for path in sorted(DIST.glob("*.zip")):
        print(path)


if __name__ == "__main__":
    main()
