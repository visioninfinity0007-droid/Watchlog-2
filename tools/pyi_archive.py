#!/usr/bin/env python3
"""Read a PyInstaller one-file EXE without running it and without PyInstaller installed.

Used by the release gates (tools/release_size_report.py, tools/verify_installer_payload.py and
the Setup UI content checks). It parses the two on-disk formats directly:

  * the CArchive (PKG) appended to the bootloader: a cookie at the end of the file
    (magic ``MEI\\014\\013\\012\\013\\016``) and a table of contents of every bundled file
    (DLLs, .pyd, data files, the PYZ, scripts), with compressed and uncompressed sizes;
  * the PYZ inside it: the byte-compiled Python modules, whose table of contents is a
    marshalled list of ``(name, (typecode, offset, length))``. Marshal of tuples, strings and
    ints is stable across Python versions, so a 3.12 build can be read from any Python 3.

Nothing here executes code from the archive: module blobs are only decompressed, never
unmarshalled, so reading the baked build SHA works on any interpreter version.
"""
from __future__ import annotations

import hashlib
import marshal
import re
import struct
import zlib
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

_COOKIE_MAGIC = b"MEI\014\013\012\013\016"
_COOKIE_FORMAT = "!8sIIII64s"
_COOKIE_LEN = struct.calcsize(_COOKIE_FORMAT)
_TOC_ENTRY_FORMAT = "!IIIIBc"
_TOC_ENTRY_LEN = struct.calcsize(_TOC_ENTRY_FORMAT)
_PYZ_MAGIC = b"PYZ\0"


class ArchiveError(RuntimeError):
    pass


@dataclass(frozen=True)
class Entry:
    name: str
    offset: int          # relative to the PKG start
    compressed: int
    uncompressed: int
    is_compressed: bool
    typecode: str


class PyInstallerExe:
    """The PKG table of contents (and the PYZ module list) of one frozen EXE."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        data = self.path.read_bytes()
        cookie_at = data.rfind(_COOKIE_MAGIC)
        if cookie_at < 0:
            raise ArchiveError(f"{self.path.name}: not a PyInstaller one-file executable")
        _magic, pkg_len, toc_off, toc_len, self.pyvers, pylib = struct.unpack(
            _COOKIE_FORMAT, data[cookie_at:cookie_at + _COOKIE_LEN])
        self.python_library = pylib.rstrip(b"\0").decode("ascii", "replace")
        end = cookie_at + _COOKIE_LEN
        self._start = end - pkg_len
        self._data = data
        toc = data[self._start + toc_off:self._start + toc_off + toc_len]
        self.entries: list[Entry] = []
        self.options: list[str] = []
        pos = 0
        while pos < len(toc):
            elen, off, clen, ulen, cflag, tcode = struct.unpack(
                _TOC_ENTRY_FORMAT, toc[pos:pos + _TOC_ENTRY_LEN])
            name = toc[pos + _TOC_ENTRY_LEN:pos + elen].rstrip(b"\0").decode("utf-8")
            pos += elen
            tcode = tcode.decode("ascii")
            if tcode == "o":
                self.options.append(name)
                continue
            self.entries.append(Entry(name, off, clen, ulen, bool(cflag), tcode))
        self._pyz_toc: dict[str, tuple] | None = None

    # --- PKG -----------------------------------------------------------------------------
    @property
    def bytes(self) -> int:
        return len(self._data)

    def sha256(self) -> str:
        return hashlib.sha256(self._data).hexdigest().upper()

    def names(self) -> list[str]:
        return [e.name for e in self.entries]

    def extract(self, name: str) -> bytes:
        for e in self.entries:
            if e.name == name:
                blob = self._data[self._start + e.offset:self._start + e.offset + e.compressed]
                return zlib.decompress(blob) if e.is_compressed else blob
        raise KeyError(name)

    # --- PYZ -----------------------------------------------------------------------------
    def _pyz_entry(self) -> Entry:
        for e in self.entries:
            if e.typecode == "z":
                return e
        raise ArchiveError(f"{self.path.name}: no PYZ archive")

    def pyz_toc(self) -> dict[str, tuple]:
        if self._pyz_toc is None:
            pyz = self._pyz_entry()
            base = self._start + pyz.offset
            if self._data[base:base + 4] != _PYZ_MAGIC:
                raise ArchiveError("PYZ magic mismatch")
            (toc_off,) = struct.unpack("!i", self._data[base + 8:base + 12])
            raw = marshal.loads(self._data[base + toc_off:base + pyz.compressed])
            self._pyz_toc = {str(k): tuple(v) for k, v in dict(raw).items()}
        return self._pyz_toc

    def pyz_modules(self) -> list[str]:
        return sorted(self.pyz_toc())

    def pyz_raw(self, module: str) -> bytes:
        """The decompressed (still marshalled) code blob of one bundled module."""
        _typecode, off, length = self.pyz_toc()[module]
        base = self._start + self._pyz_entry().offset
        return zlib.decompress(self._data[base + off:base + off + length])

    def baked_build_sha(self) -> str | None:
        """The BUILD_SHA stamped into the bundled build_info module, or None if absent/empty."""
        if "build_info" not in self.pyz_toc():
            return None
        found = re.findall(rb"(?<![0-9a-f])[0-9a-f]{40}(?![0-9a-f])", self.pyz_raw("build_info"))
        return found[0].decode("ascii") if found else None


# --- components (sizes are reported per component, never used as integrity proof) -----------
COMPONENT_RULES = [
    ("Qt WebEngine", r"qtwebengine|webengine"),
    ("Qt QML/Quick", r"qt6qml|qt6quick|/qml/|qtquick"),
    ("Qt 3D", r"qt63d|3d(core|render|input|logic|animation|extras)"),
    ("Qt Multimedia", r"multimedia|qt6spatialaudio|avcodec|avformat|avutil|swscale|swresample"),
    ("Qt translations", r"translations/.*\.qm$"),
    ("Qt plugins", r"pyside6/plugins|qt6/plugins|/plugins/"),
    ("Qt Designer/tools", r"designer|assistant|linguist|lupdate|lrelease|uic\.exe|rcc\.exe|qmllint|qmlformat|qmlls"),
    ("Qt PDF", r"qt6pdf"),
    ("Qt core libs (Core/Gui/Widgets/Network/Svg)", r"qt6(core|gui|widgets|network|svg|opengl|dbus|xml|concurrent|printsupport)"),
    ("Qt other libs", r"qt6|pyside6|shiboken6"),
    ("onnxruntime", r"onnxruntime"),
    ("ONNX model", r"\.onnx$"),
    ("numpy", r"numpy"),
    ("FFmpeg (imageio-ffmpeg)", r"imageio_ffmpeg|ffmpeg"),
    ("OpenCV", r"cv2|opencv"),
    ("Pillow", r"pil/|_imaging|pillow"),
    ("cryptography/OpenSSL", r"cryptography|libcrypto|libssl|_rust"),
    ("VC runtime", r"vcruntime|msvcp|concrt|vcomp|ucrtbase|api-ms-win"),
    ("Python runtime", r"python3\d*\.dll|^python|base_library|_socket|_ssl|_hashlib|select\.pyd|unicodedata|_ctypes|libffi|pyexpat|_bz2|_lzma|_decimal|_elementtree|_overlapped|_queue|_asyncio|_multiprocessing|_uuid|_wmi|_zoneinfo"),
    ("tzdata", r"tzdata"),
    ("certifi", r"certifi|cacert"),
    ("psutil", r"psutil"),
]
PYZ_COMPONENT = "PYZ (pure-Python modules)"


def component(name: str) -> str:
    n = name.lower().replace("\\", "/")
    for label, rx in COMPONENT_RULES:
        if re.search(rx, n):
            return label
    return "other"


def components(exe: PyInstallerExe) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = defaultdict(lambda: {"compressed": 0, "uncompressed": 0, "files": 0})
    for e in exe.entries:
        label = PYZ_COMPONENT if e.typecode == "z" else component(e.name)
        out[label]["compressed"] += e.compressed
        out[label]["uncompressed"] += e.uncompressed
        out[label]["files"] += 1
    return dict(sorted(out.items(), key=lambda kv: -kv[1]["compressed"]))


def qt_plugins(exe: PyInstallerExe) -> dict[str, list[str]]:
    """{plugin family: [plugin file names]} for PySide6/plugins/<family>/<file>."""
    out: dict[str, list[str]] = defaultdict(list)
    for name in exe.names():
        parts = name.replace("\\", "/").split("/")
        low = [p.lower() for p in parts]
        if len(parts) >= 4 and low[0] == "pyside6" and low[1] == "plugins":
            out[parts[2]].append(parts[-1])
    return {k: sorted(v) for k, v in sorted(out.items())}


def qt_libraries(exe: PyInstallerExe) -> list[str]:
    """Qt6*.dll / PySide6 extension modules bundled at the archive's PySide6 level."""
    out = []
    for name in exe.names():
        base = name.replace("\\", "/").split("/")[-1]
        if re.match(r"(?i)^qt6\w*\.dll$", base) or re.match(r"(?i)^qt\w+\.pyd$", base):
            out.append(base)
    return sorted(set(out))


# --- PE version resource (no pywin32 / pefile needed) -----------------------------------------
def pe_image_end(data: bytes) -> int:
    """End of the PE image proper (last section's raw data); appended overlays (a PyInstaller
    PKG, an NSIS payload, an Authenticode certificate) start after it. len(data) if unparsable."""
    try:
        if data[:2] != b"MZ":
            return len(data)
        lfanew = struct.unpack_from("<I", data, 0x3C)[0]
        if data[lfanew:lfanew + 4] != b"PE\0\0":
            return len(data)
        sections, = struct.unpack_from("<H", data, lfanew + 6)
        opt_size, = struct.unpack_from("<H", data, lfanew + 20)
        table = lfanew + 24 + opt_size
        end = 0
        for i in range(sections):
            size, ptr = struct.unpack_from("<II", data, table + 40 * i + 16)
            end = max(end, ptr + size)
        return end or len(data)
    except struct.error:
        return len(data)


def pe_version_string(data: bytes, key: str) -> str | None:
    """A VS_VERSIONINFO StringFileInfo value (ProductVersion, FileVersion...), searched only in
    the PE image so bytes inside an appended archive can never be mistaken for it."""
    image = data[:pe_image_end(data)]
    needle = (key + "\0").encode("utf-16-le")
    i = image.rfind(needle)
    if i < 0:
        return None
    j = i + len(needle)
    while j + 1 < len(image) and image[j:j + 2] == b"\0\0":
        j += 2
    end = j
    while end + 1 < len(image) and image[end:end + 2] != b"\0\0":
        end += 2
    return image[j:end].decode("utf-16-le", "replace").strip() or None
