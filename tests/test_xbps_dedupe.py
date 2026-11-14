import unittest

from backend.dedupe import dedupe_xbps, xbps_keys
from backend.models import Package, PackageSource, PackageStatus


def _p(name, repo="", status=PackageStatus.NOT_INSTALLED, source=PackageSource.XBPS):
    return Package(id=f"{source.value}:{name}", name=name, summary="", description="",
                   version="1", source=source, status=status, repository=repo)


class XbpsDedupeTests(unittest.TestCase):
    def test_keeps_one_and_prefers_current_over_nonfree(self):
        out = dedupe_xbps([_p("vim", "nonfree"), _p("vim", "current"), _p("nano")])
        self.assertEqual([p.id for p in out], ["xbps:vim", "xbps:nano"])
        self.assertEqual(out[0].repository, "current")

    def test_prefers_installed(self):
        out = dedupe_xbps([_p("vim", "current"), _p("vim", "multilib", PackageStatus.INSTALLED)])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].status, PackageStatus.INSTALLED)

    def test_streaming_drops_late_duplicates(self):
        first = [_p("vim")]
        out = dedupe_xbps([_p("vim"), _p("git")], xbps_keys(first))
        self.assertEqual([p.id for p in out], ["xbps:git"])

    def test_other_sources_untouched(self):
        flat = [_p("vim", source=PackageSource.FLATPAK), _p("vim", source=PackageSource.FLATPAK)]
        self.assertEqual(len(dedupe_xbps(flat)), 2)


if __name__ == "__main__":
    unittest.main()
