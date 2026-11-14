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
            self._pump(lambda: len(page._items) == 42)
            # La lista está virtualizada: solo existen widgets para lo visible.
            self.assertLessEqual(len(page._cards()), 42)
            self.assertGreaterEqual(len(page._cards()), 1)
            # Al terminar, los resultados quedan ordenados por relevancia
            # aunque gedit/gimp hayan llegado después en el streaming.
            def ordered():
                import ui.search_page as sp
                scores = [sp._relevance_key(p, page._last_query) for p in page._items]
                return len(scores) == 42 and scores == sorted(scores)
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

    def test_large_source_results_are_emitted_in_batches_of_at_most_200(self):
        import ui.search_page as sp
        packages = [_pkg(PackageSource.APPIMAGE, f"app-{i:03d}")
                    for i in range(450)]
        worker = sp.SearchWorker("app")
        emitted = []
        worker.source_ready.connect(
            lambda source, batch: emitted.append(("replace", source, len(batch)))
        )
        worker.packages_found.connect(
            lambda source, batch: emitted.append(("append", source, len(batch)))
        )

        worker._emit_batched("appimage", packages)

        self.assertEqual(emitted, [
            ("replace", "appimage", 200),
            ("append", "appimage", 200),
            ("append", "appimage", 50),
        ])

    def test_relevance_places_exact_matches_first_and_keeps_discovery_order_on_ties(self):
        import ui.search_page as sp
        exact = _pkg(PackageSource.XBPS, "firefox")
        exact.name = "Firefox"
        prefix = _pkg(PackageSource.XBPS, "firefox-nightly")
        prefix.name = "Firefox Nightly"
        word_prefix = _pkg(PackageSource.FLATPAK, "mozilla-firefox")
        word_prefix.name = "Mozilla Firefox"
        first_tie = _pkg(PackageSource.XBPS, "summary-one")
        first_tie.name = "Browser One"
        first_tie.summary = "Firefox utility"
        second_tie = _pkg(PackageSource.FLATPAK, "summary-two")
        second_tie.name = "Browser Two"
        second_tie.summary = "Firefox utility"
        page = sp.SearchPage()
        page._sort_mode = "relevance"
        page._last_query = "firefox"
        ordered = page._apply_sort([second_tie, word_prefix, first_tie, prefix, exact])
        self.assertEqual([p.name for p in ordered], [
            "Firefox", "Firefox Nightly", "Mozilla Firefox", "Browser Two", "Browser One",
        ])
        accented = _pkg(PackageSource.XBPS, "facil")
        accented.name = "Fácil"
        self.assertEqual(sp._relevance_key(accented, "facil")[0], 0)
        japanese = _pkg(PackageSource.FLATPAK, "tokyo")
        japanese.name = "東京電機"
        self.assertEqual(sp._relevance_key(japanese, "東京電機")[0], 0)
        page.shutdown_workers()

    def test_enabled_multilib_preference_filters_32bit_search_results(self):
        import ui.search_page as sp
        multilib = _pkg(PackageSource.XBPS, "legacy-app")
        multilib.repository = "multilib"
        regular = _pkg(PackageSource.XBPS, "regular-app")
        page = sp.SearchPage()
        page._show_hidden = True
        with mock.patch("core.settings.get_bool", return_value=True):
            visible = page._filter_results([multilib, regular])
        self.assertEqual([pkg.name for pkg in visible], ["regular-app"])
        page.shutdown_workers()

    def test_cached_search_package_is_rechecked_after_blacklist_file_changes(self):
        import ui.search_page as sp
        from backend import blacklist
        package = _pkg(PackageSource.XBPS, "temporary-cache-app")
        page = sp.SearchPage()
        page._show_hidden = False
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "blacklist_xbps.kn"
            with mock.patch.object(blacklist, "_BLACKLIST_PATH", path), \
                 mock.patch.object(blacklist, "_loaded", False), \
                 mock.patch.object(blacklist, "_mtime", None), \
                 mock.patch.object(blacklist, "_size", None), \
                 mock.patch.object(blacklist, "_rules_name", []), \
                 mock.patch.object(blacklist, "_rules_id", []), \
                 mock.patch.object(blacklist, "_rules_cat", []):
                path.write_text("name:temporary-cache-app\n", encoding="utf-8")
                self.assertEqual(page._filter_results([package]), [])
                path.write_text("# rule removed\n", encoding="utf-8")
                self.assertEqual(page._filter_results([package]), [package])
        page.shutdown_workers()

    def test_search_text_change_starts_live_search_and_opens_results(self):
        from types import SimpleNamespace
        from ui.main_window import MainWindow, PAGE_EXPLORE, PAGE_SEARCH

        class Stack:
            def __init__(self):
                self.index = PAGE_EXPLORE

            def currentIndex(self):
                return self.index

            def setCurrentIndex(self, index):
                self.index = index

        class SearchPageStub:
            def __init__(self):
                self.queries = []

            def trigger_search(self, query):
                self.queries.append(query)

        stack = Stack()
        page = SearchPageStub()
        syncs = []
        stub = SimpleNamespace(
            _stack=stack, _search_page=page, _prev_page=None,
            _sync_nav_selection=lambda target: syncs.append(target),
            _sync_search_filters=lambda: None,
        )
        MainWindow._on_search_changed(stub, "fire")
        self.assertEqual(page.queries, ["fire"])
        self.assertEqual(stack.currentIndex(), PAGE_SEARCH)
        self.assertEqual(stub._prev_page, PAGE_EXPLORE)


if __name__ == "__main__":
    unittest.main()
