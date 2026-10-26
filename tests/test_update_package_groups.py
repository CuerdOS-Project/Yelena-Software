import unittest

from backend.blacklist import is_blacklisted
from backend.models import Package, PackageSource
from backend.update_groups import update_package_group


class UpdatePackageGroupTests(unittest.TestCase):
    def test_system_package_families_are_grouped(self):
        cases = {
            "linux6.12": "cuerdos_base",
            "xfce4-panel": "desktop",
            "python3": "python3",
            "python3-pip": "python3",
            "python3.12": "python3",
            "qemu": "qemu",
            "qemu-system-x86_64": "qemu",
            "xorg-server": "xorg",
            "xorgproto": "xorg",
            "xf86-video-amdgpu": "xf86",
            "xf86-input-libinput": "xf86",
            "mesa": "other",
        }
        for package, expected_group in cases.items():
            with self.subTest(package=package):
                self.assertEqual(update_package_group(package), expected_group)

    def test_system_component_families_are_hidden_from_main_package_search(self):
        names = (
            "python3",
            "python3-pip",
            "python3.12",
            "qemu",
            "qemu-system-x86_64",
            "xorg-server",
            "xf86-video-amdgpu",
        )
        for name in names:
            package = Package(
                id=f"xbps:{name}",
                name=name,
                summary="",
                source=PackageSource.XBPS,
            )
            with self.subTest(package=name):
                self.assertTrue(is_blacklisted(package))


if __name__ == "__main__":
    unittest.main()
