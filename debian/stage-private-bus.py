"""Stage the same versioned supervisor and policy as the shared SDK installer."""
from pathlib import Path
import runpy
import subprocess

source = Path(__file__).resolve().parents[1]
package = source / "debian/kilix"
installer = runpy.run_path(str(source / "scripts/install-private-bus-supervisor.py"))
for item in installer["plan"](source):
    target = package / Path(item["target"]).relative_to("/")
    subprocess.run(["install", "-D", "-m", format(item["mode"], "o"), "/dev/stdin", target],
                   input=item["data"], check=True)
