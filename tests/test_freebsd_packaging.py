"""The FreeBSD meta-port pulls in everything the pkg installer installs."""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PORT = ROOT / "packaging" / "freebsd" / "kilix-deps"
INSTALLER = ROOT / "scripts" / "install-build-deps.sh"


def installer_packages():
    text = INSTALLER.read_text()
    body = text[text.index("freebsd_install() {"):]
    listed = re.search(r'local pkgs="([^"]*)"', body).group(1)
    return set(listed.replace("\\\n", " ").split())


def port_dependencies():
    """Package names from RUN_DEPENDS, with the Python flavour prefix as py312-."""
    text = PORT.joinpath("Makefile").read_text().replace("\\\n", " ")
    names = set()
    for line in text.splitlines():
        if not re.match(r"RUN_DEPENDS\+?=", line):
            continue
        for entry in line.split("=", 1)[1].split():
            target = entry.split(":", 1)[0]
            names.add(target.replace("${PYTHON_PKGNAMEPREFIX}", "py312-").split(">", 1)[0])
    return names


class FreeBSDPortTests(unittest.TestCase):
    def test_every_installed_package_is_a_port_dependency(self):
        dependencies = port_dependencies()
        # USES=python:run supplies the interpreter itself.
        missing = installer_packages() - dependencies - {"python312"}
        self.assertEqual(missing, set(), "add these to packaging/freebsd/kilix-deps")

    def test_the_port_uses_python_and_is_a_metaport(self):
        makefile = PORT.joinpath("Makefile").read_text()
        self.assertRegex(makefile, r"(?m)^USES=\s+metaport python:run$")
        self.assertTrue(PORT.joinpath("pkg-descr").read_text().strip())


if __name__ == "__main__":
    unittest.main()
