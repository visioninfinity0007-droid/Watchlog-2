"""Which PySide6 submodules does PyInstaller's collect_all('PySide6') pick up in this environment?"""
import json, platform, sys
from importlib import metadata
from PyInstaller.utils.hooks import collect_submodules, collect_dynamic_libs
mods = sorted(collect_submodules("PySide6"))
libs = collect_dynamic_libs("PySide6")
addons = [m for m in mods if any(k in m for k in ("WebEngine", "Qt3D", "Quick", "Qml", "Multimedia", "Designer", "DataVisualization", "Graphs", "Charts", "Pdf"))]
pkgs = {d: (lambda n: metadata.version(n) if n else None)(d) for d in ()}
installed = sorted(f"{d.metadata['Name']}=={d.version}" for d in metadata.distributions()
                   if d.metadata['Name'].lower() in ("pyside6", "pyside6-essentials", "pyside6-addons", "shiboken6", "numpy", "onnxruntime", "pillow", "imageio-ffmpeg", "pyinstaller", "pyinstaller-hooks-contrib"))
out = {"python": sys.version, "platform": platform.platform(), "installed": installed,
       "submodules_total": len(mods), "addon_like_submodules": addons, "dynamic_libs": len(libs),
       "dynamic_lib_bytes": sum(__import__("os").path.getsize(s) for s, _ in libs)}
print(json.dumps(out, indent=1))
