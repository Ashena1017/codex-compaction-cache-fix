"""Build the small windowless desktop launcher (requires PyInstaller)."""
from pathlib import Path
import subprocess
import sys

source = Path(__file__).resolve().parent
install = source.parent if source.name == "app" else source
subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--onefile", "--windowed",
                "--name", "CodexCompactionFix", "--distpath", str(install),
                "--workpath", str(install/"data"/"build"),
                "--specpath", str(install/"data"/"build"), str(source/"shell_launcher.py")], check=True)
