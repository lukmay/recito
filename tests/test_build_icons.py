"""Icon pipeline: tools/build_icons.py and the committed assets/icons/.

The committed-output checks only parse files and run everywhere. The build
test needs PySide6 (QtSvg) and is skipped without it, as in CI."""
import importlib.util
import struct
from pathlib import Path

import pytest

import tts_icons

REPO = Path(__file__).resolve().parent.parent
ICONS = REPO / "assets" / "icons"
TOOL = REPO / "tools" / "build_icons.py"
ICO_SIZES = [16, 20, 24, 32, 40, 48, 64, 256]


def _tool():
    spec = importlib.util.spec_from_file_location("build_icons", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def png_size(path: Path):
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n", path
    return struct.unpack(">II", data[16:24])


def ico_layers(path: Path):
    """[(entry size, size from the image data)] of every ICO layer."""
    data = path.read_bytes()
    reserved, kind, count = struct.unpack("<HHH", data[:6])
    assert (reserved, kind) == (0, 1)
    out = []
    for i in range(count):
        w, h, _, _, planes, bpp, length, offset = struct.unpack(
            "<BBBBHHII", data[6 + 16 * i:22 + 16 * i])
        size = w or 256
        assert w == h and planes == 1 and bpp == 32
        blob = data[offset:offset + length]
        assert len(blob) == length
        if blob[:8] == b"\x89PNG\r\n\x1a\n":
            real = struct.unpack(">I", blob[16:20])[0]
        else:                                   # BITMAPINFOHEADER, height doubled
            hdr, bw, bh = struct.unpack("<Iii", blob[:12])
            assert hdr == 40 and bh == 2 * bw
            real = bw
        out.append((size, real))
    return out


def expected_files(tool):
    files = ["app.ico", f"hicolor/scalable/apps/{tool.APP_NAME}.svg"]
    files += [f"hicolor/{n}x{n}/apps/{tool.APP_NAME}.png" for n in tool.HICOLOR_SIZES]
    for variant in tool.TRAY_VARIANTS:
        for state in tool.TRAY_STATES:
            stem = f"tray-{variant}" + (f"-{state}" if state else "")
            files += [f"tray/{stem}-{n}.png" for n in tool.TRAY_SIZES]
    files += [f"hicolor/scalable/status/{tts_icons.tray_theme_name(s)}.svg"
              for s in tts_icons.STATES]
    return files


def check_output(out: Path, tool):
    for rel in expected_files(tool):
        assert (out / rel).is_file(), rel
    assert ico_layers(out / "app.ico") == [(n, n) for n in ICO_SIZES]
    for n in tool.HICOLOR_SIZES:
        assert png_size(out / "hicolor" / f"{n}x{n}" / "apps" / f"{tool.APP_NAME}.png") == (n, n)
    for p in (out / "tray").glob("*.png"):
        n = int(p.stem.rsplit("-", 1)[1])
        assert png_size(p) == (n, n), p.name
    for p in (out / "hicolor" / "scalable" / "status").glob("*.svg"):
        assert b'id="current-color-scheme"' in p.read_bytes(), p.name


def test_runtime_names_match_the_pipeline():
    tool = _tool()
    assert tool.APP_NAME == tts_icons.APP_NAME and tool.TRAY_NAME == tts_icons.TRAY_NAME
    assert tool.TRAY_SIZES == tts_icons.TRAY_SIZES
    assert tool.HICOLOR_SIZES == tts_icons.APP_SIZES
    assert tool.ICO_SIZES == tuple(ICO_SIZES)
    assert [s or "idle" for s in tool.TRAY_STATES] == list(tts_icons.STATES)


@pytest.mark.skipif(not (ICONS / "app.ico").is_file(), reason="icons not generated yet")
def test_committed_icons_complete():
    check_output(ICONS, _tool())


def test_pick_source_prefers_tuned_drawings(tmp_path):
    tool = _tool()
    for name in ("app-icon.svg", "app-icon-16.svg", "app-icon-24.svg", "app-icon-32.svg"):
        (tmp_path / name).write_text("<svg/>")
    pick = {n: tool.pick_source(tmp_path, "app-icon", n).name for n in ICO_SIZES + [22]}
    assert pick == {16: "app-icon-16.svg", 20: "app-icon-24.svg", 22: "app-icon-24.svg",
                    24: "app-icon-24.svg", 32: "app-icon-32.svg", 40: "app-icon.svg",
                    48: "app-icon.svg", 64: "app-icon.svg", 256: "app-icon.svg"}


def test_symbolic_conversion(tmp_path):
    tool = _tool()
    src = tmp_path / "s.svg"
    src.write_text('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16">'
                   '<path d="M0 0h8v8z" fill="#bebebe"/><rect class="error" width="2" height="2" '
                   'style="fill:#f00"/><circle r="1"/></svg>')
    out = tool.symbolic_svg(src).decode()
    assert 'id="current-color-scheme"' in out and "#bebebe" not in out and "#f00" not in out
    assert out.count("ColorScheme-Text") >= 3            # style rule plus path and circle
    assert "error ColorScheme-NegativeText" in out
    kde = tmp_path / "k.svg"
    kde.write_text('<svg><style id="current-color-scheme"/></svg>')
    assert tool.symbolic_svg(kde) == kde.read_bytes()     # already Plasma-ready: unchanged


def _brand(path: Path):
    """Placeholder sources, so the build test does not depend on the design."""
    path.mkdir()
    (path / "app-icon.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256">'
        '<rect x="8" y="8" width="240" height="240" rx="56" fill="#6750a4"/></svg>')
    (path / "app-icon-16.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16">'
        '<rect width="16" height="16" fill="#3f2a78"/></svg>')
    for variant in ("light", "dark", "symbolic"):
        for state in ("", "-speaking", "-paused", "-error"):
            (path / f"tray-{variant}{state}.svg").write_text(
                '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16">'
                '<path d="M1 2h14v10H6l-5 3z" fill="#1c1b1f"/></svg>')


def test_build_renders_every_file_and_is_deterministic(tmp_path):
    pytest.importorskip("PySide6.QtSvg")
    tool = _tool()
    brand = tmp_path / "brand"
    _brand(brand)
    tool.build(brand, tmp_path / "a")
    check_output(tmp_path / "a", tool)
    assert tool.check(brand, tmp_path / "a") == []
    # The 16 px layer comes from the tuned drawing (opaque corner pixel),
    # the master has a rounded, transparent corner.
    from PySide6.QtGui import QImage
    small = QImage(str(tmp_path / "a" / "hicolor" / "16x16" / "apps" / "recito.png"))
    big = QImage(str(tmp_path / "a" / "hicolor" / "48x48" / "apps" / "recito.png"))
    assert small.pixelColor(0, 0).alpha() == 255 and big.pixelColor(0, 0).alpha() == 0
