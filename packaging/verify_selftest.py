"""Run the shipped diagnostic with bundled runtimes, without developer PATH."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile

parser = argparse.ArgumentParser()
parser.add_argument("--directory", required=True)
parser.add_argument("--output", required=True)
parser.add_argument("--network", action="store_true")
args = parser.parse_args()
app = Path(args.directory).resolve()
target = Path(args.output).resolve()
target.parent.mkdir(parents=True, exist_ok=True)
env = {k: v for k, v in os.environ.items() if not k.startswith("MEDIAWORKBENCH_") and k not in ("PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV")}
env.update(PYTHONUTF8="1", PYTHONIOENCODING="utf-8", PLAYWRIGHT_BROWSERS_PATH=str(app / "runtime/browsers"))
env["PATH"] = str(app / "runtime/node") + os.pathsep + os.environ.get("SystemRoot", "C:\\Windows") + "\\System32"
code = '''
import json,sys,time
from pathlib import Path
from workbench.selftest import SelfTests
s=SelfTests(Path(sys.argv[1]))
try:
 s.start(sys.argv[3]=='yes')
 while s.active():time.sleep(.2)
 r=s.view()
 Path(sys.argv[2]).write_text(json.dumps(r,ensure_ascii=False,indent=2),encoding='utf-8')
 assert r['state']=='completed' and r['local_state']=='passed' and r['cleanup_complete'], r
finally:s.close()
'''
with tempfile.TemporaryDirectory(prefix="MediaWorkbench 自检 ") as temporary:
    subprocess.run([str(app / "runtime/python/python.exe"), "-c", code, temporary, str(target), "yes" if args.network else "no"],
                   env=env, cwd=app, check=True, timeout=210)
print(json.dumps({"ok": True, "result": str(target)}))
