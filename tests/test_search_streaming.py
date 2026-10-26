"""Búsqueda en streaming: los paquetes aparecen según llegan y las fuentes
corren en paralelo (una fuente lenta no retrasa a las rápidas)."""
from __future__ import annotations

import os
import sys
import time
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from backend import line_stream, xbps_backend, flatpak_backend
from backend.models import Package, PackageSource, PackageStatus


def _script(body: str) -> str:
    fd, path = tempfile.mkstemp(suffix=".sh")
    with os.fdopen(fd, "w") as fh:
        fh.write("#!/bin/sh\n" + textwrap.dedent(body))
    os.chmod(path, 0o755)
    return path


class LineStreamTests(unittest.TestCase):
    def test_lines_arrive_before_process_ends(self):
        path = _script("echo uno\nsleep 1\necho dos\n")
        t0 = time.monotonic()
        it = line_stream.iter_lines([path])
        first = next(it)
        elapsed = time.monotonic() - t0
        self.assertEqual(first, "uno")
        self.assertLess(elapsed, 0.8, "la primera línea no debe esperar al final")
        self.assertEqual(list(it), ["dos"])

    def test_should_stop_kills_process(self):
        path = _script("echo uno\nsleep 30\n")
        stop = {"v": False}
        it = line_stream.iter_lines([path], should_stop=lambda: stop["v"])
        self.assertEqual(next(it), "uno")
        stop["v"] = True
        t0 = time.monotonic()
        self.assertEqual(list(it), [])
        self.assertLess(time.monotonic() - t0, 3)

    def test_timeout_raises(self):
        path = _script("sleep 30\n")
        with self.assertRaises(line_stream.StreamTimeout):
            list(line_stream.iter_lines([path], timeout=0.3))

    def test_missing_binary_is_empty(self):
        self.assertEqual(list(line_stream.iter_lines(["/no/such/binary"])), [])

    def test_idle_heartbeat(self):
        path = _script("sleep 0.6\necho x\n")
        items = list(line_stream.iter_lines([path], yield_idle=True))
        self.assertIn(None, items)
        self.assertEqual(items[-1], "x")


class ParserTests(unittest.TestCase):
    def test_xbps_line(self):
        pkg = xbps_backend._parse_search_line(
            "[*] vim-9.0.0472_1  Vi IMproved, a highly configurable text editor")
        self.assertEqual((pkg.name, pkg.version, pkg.status),
                         ("vim", "9.0.0472_1", PackageStatus.INSTALLED))
        pkg = xbps_backend._parse_search_line("[-] nano-7.2_1  Small editor")
        self.assertEqual(pkg.status, PackageStatus.NOT_INSTALLED)
        self.assertIsNone(xbps_backend._parse_search_line(""))

    def test_flatpak_line(self):
        line = "GIMP\tImage editor\torg.gimp.GIMP\t2.10\tflathub"
        pkg = flatpak_backend._package_from_search_line(line, {"org.gimp.GIMP"})
        self.assertEqual(pkg.appstream_id, "org.gimp.GIMP")
        self.assertEqual(pkg.status, PackageStatus.INSTALLED)
        self.assertIn("flathub", pkg.icon_url)
        self.assertIsNone(flatpak_backend._package_from_search_line("Name\tDesc\tid", set()))

    def test_xbps_iter_search_matches_search(self):
        out = "[-] a-1.0_1  A\n[*] b-2.0_1  B\nbasura\n"
        path = _script(f"printf '%s' '{out}'\n")
        with mock.patch.object(xbps_backend, "_XBPS_QUERY", path):
            got = [p.name for p in xbps_backend.iter_search("x") if p is not None]
        self.assertEqual(got, ["a", "b"])


def _pkg(source, name):
    return Package(id=f"{source.value}:{name}", name=name, summary=f"{name} desc",
                   description="", version="1.0", source=source,
                   status=PackageStatus.NOT_INSTALLED, icon_name=name, category="Other")


class SearchPageStreamingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def _pump(self, cond, timeout=8.0):
        t0 = time.monotonic()
        while time.monotonic() - t0 < timeout:
            self.app.processEvents()
            if cond():
                return time.monotonic() - t0
            time.sleep(0.005)
        self.fail("timeout esperando la condición")

    def test_fast_source_not_blocked_by_slow_one_and_items_trickle(self):
        from core import search_cache
        search_cache.invalidate()

        def slow_flatpak(q, limit=5000, should_stop=None):
            t_end = time.monotonic() + 1.5
            while time.monotonic() < t_end:
                if should_stop and should_stop():
                    return
                yield None
                time.sleep(0.05)
            for n in ("gimp", "gedit"):
                yield _pkg(PackageSource.FLATPAK, n)

        def trickle_xbps(q, limit=5000, should_stop=None):
            for i in range(40):
                yield _pkg(PackageSource.XBPS, f"pkg{i:02d}")
                time.sleep(0.01)

        import ui.search_page as sp
        with mock.patch.object(sp, "show_flatpak", lambda: True), \
             mock.patch.object(sp, "show_xbps", lambda: True), \
             mock.patch.object(sp, "show_appimage", lambda: False), \
             mock.patch("core.settings.get_bool", lambda k, *a, **kw: True), \
             mock.patch("core.catalog.load_installed_index",
                        lambda: {"flatpak_ids": set(), "flatpak_names": set(),
                                 "xbps_ids": set(), "xbps_names": set()}), \
             mock.patch("engine.package_engine.get_active_engine", lambda: "yelena-buddies"), \
             mock.patch.object(flatpak_backend, "iter_search", slow_flatpak), \
             mock.patch.object(xbps_backend, "iter_search", trickle_xbps), \
             mock.patch.object(xbps_backend, "xbps_available", lambda: True):
            page = sp.SearchPage()
            page.resize(900, 700)
            page.show()
            seen_counts = []
            page.counts_changed.connect(lambda c: seen_counts.append(dict(c)))
            t0 = time.monotonic()
            page.trigger_search("g")
            page.force_search()

            t_first = self._pump(lambda: len(page._cards()) >= 1)
            self.assertLess(t_first, 0.8, "el primer paquete debe verse enseguida")
            self.assertEqual(page._results_by_source.get("flatpak"), None,
                             "flatpak aún no respondió; no debe bloquear a XBPS")

            # XBPS completo mientras Flatpak sigue buscando
            self._pump(lambda: len(page._results_by_source.get("xbps", [])) == 40)
            self.assertLess(time.monotonic() - t0, 1.4)
            self.assertTrue(page._search_active)

            # Hubo varias actualizaciones parciales (goteo), no una sola
            xbps_steps = {c["xbps"] for c in seen_counts if c.get("xbps")}
            self.assertGreaterEqual(len(xbps_steps), 3, xbps_steps)

            self._pump(lambda: not page._search_active)
            self.assertEqual(len(page._results_by_source["flatpak"]), 2)
            self._pump(lambda: len(page._cards()) == 42)
            # al terminar, las tarjetas quedan ordenadas A-Z (gedit/gimp se
            # colaron al final del goteo y se recolocan)
            def ordered():
                names = [c.pkg.name.lower() for c in page._cards()]
                return len(names) == 42 and names == sorted(names)
            self._pump(ordered, timeout=3)
            page.shutdown_workers()

    def test_filter_bar_moves_into_host_and_back(self):
        import ui.search_page as sp
        from PySide6.QtWidgets import QWidget, QHBoxLayout
        with mock.patch.object(sp, "show_flatpak", lambda: True), \
             mock.patch.object(sp, "show_xbps", lambda: True), \
             mock.patch.object(sp, "show_appimage", lambda: False):
            page = sp.SearchPage()
            host = QWidget(); QHBoxLayout(host)
            page.attach_filter_bar(host)
            self.assertIs(page._filter_bar.parent(), host)
            page.detach_filter_bar()
            self.assertIs(page._filter_bar.parent(), page)
            self.assertIs(page._root_layout.itemAt(0).widget(), page._filter_bar)
            page.shutdown_workers()


if __name__ == "__main__":
    unittest.main()
