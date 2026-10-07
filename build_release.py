"""Build a clean Windows ZIP from tracked source, never from private runtime data."""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile


SETUP_CMD = '''@echo off
setlocal
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"
where py >nul 2>nul
if errorlevel 1 goto use_python
py -3 "%~dp0app\\release_setup.py" %*
goto done
:use_python
python "%~dp0app\\release_setup.py" %*
:done
if errorlevel 1 echo Setup failed. See docs\\INSTALL.md. Python 3.11+ with Tcl/Tk is required.
pause
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", default="1.2.0")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    source = Path(__file__).resolve().parent
    git = lambda *a: subprocess.check_output(["git", "-C", str(source), *a])
    if git("status", "--porcelain").strip():
        raise SystemExit("Commit source changes before building a release.")
    sha = git("rev-parse", "HEAD").decode().strip()
    names = git("ls-tree", "-r", "--name-only", "-z", "HEAD").decode().split("\0")
    args.output.mkdir(parents=True, exist_ok=True)
    basename = f"CodexCompactionFix-{args.version}-windows"
    archive = args.output / (basename + ".zip")
    with tempfile.TemporaryDirectory(prefix="compact-release-") as temporary:
        work = Path(temporary)
        package = work / basename
        (package/"app"/"tests").mkdir(parents=True)
        (package/"data").mkdir()
        (package/"shortcuts").mkdir()
        (package/"docs").mkdir()
        for name in filter(None, names):
            if name.endswith(".py") and "/" not in name:
                target = package/"app"/("tests/"+name if name.startswith("test_") else name)
            elif name in {"requirements.txt", "LICENSE"}:
                target = package/name
            elif name == "README.md":
                target = package/"docs"/"README.md"
            elif name.startswith("evidence/") or name == "docs/INSTALL.md":
                target = package/"docs"/name.removeprefix("docs/")
            else:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(git("show", "HEAD:"+name))
        (package/"app"/"tests"/"__init__.py").write_text("", encoding="utf-8")
        (package/"setup.cmd").write_bytes(SETUP_CMD.replace("\n", "\r\n").encode("ascii"))
        (package/"VERSION.txt").write_text(f"Version: {args.version}\nSource commit: {sha}\n", encoding="utf-8")
        entries = {
            "01-启动修复": "control.py start", "02-检查状态": "control.py status",
            "03-恢复直连": "control.py restore", "04-停止代理": "control.py stop",
            "05-查看压缩记录": "control.py usage", "07-开机自启": "autostart.py menu",
            "08-CCSwitch更新后启用": "ccswitch_enable.py",
        }
        for name, command in entries.items():
            script, *options = command.split()
            text = '@echo off\nsetlocal\nchcp 65001 >nul\nset PYTHONUTF8=1\ncd /d "%~dp0..\\app"\n"%~dp0..\\runtime\\Scripts\\python.exe" "%~dp0..\\app\\'+script+'" '+" ".join(options)+'\npause\n'
            (package/"shortcuts"/(name+".cmd")).write_bytes(text.replace("\n", "\r\n").encode("utf-8"))
        check = '@echo off\nsetlocal\ncd /d "%~dp0..\\app"\n"%~dp0..\\runtime\\Scripts\\python.exe" -m unittest discover -v -s tests -t .\npause\n'
        (package/"shortcuts"/"06-本地自检.cmd").write_bytes(check.replace("\n", "\r\n").encode("ascii"))
        (package/"shortcuts"/"09-打开控制面板.cmd").write_bytes(b'@echo off\r\nstart "" "%~dp0..\\CodexCompactionFix.exe"\r\n')
        subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile", "--windowed",
                        "--name", "CodexCompactionFix", "--distpath", str(package),
                        "--workpath", str(work/"build"), "--specpath", str(work),
                        str(package/"app"/"shell_launcher.py")], check=True)
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as output:
            for path in sorted(package.rglob("*")):
                if path.is_file():
                    output.write(path, path.relative_to(work))
    checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
    (args.output/"SHA256SUMS.txt").write_text(f"{checksum}  {archive.name}\n", encoding="ascii")
    print(f"Built {archive}\nSHA256 {checksum}\nSource {sha}")


if __name__ == "__main__":
    main()
