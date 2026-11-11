"""One-click setup, run by 一键安装.bat:  python -m mailer.setup_env

Checks the Python version, installs requirements.txt (falling back to domestic
PyPI mirrors when the official index is slow or blocked) and, on Windows, puts a
shortcut to 邮件助手.pyw on the desktop."""

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REQUIREMENTS = ROOT / "requirements.txt"
LAUNCHER = ROOT / "邮件助手.pyw"
MIN_PYTHON = (3, 11)
# The official index first; company networks often reach only the mirrors.
INDEXES = [
    ("官方源", None),
    ("清华镜像", "https://pypi.tuna.tsinghua.edu.cn/simple"),
    ("阿里云镜像", "https://mirrors.aliyun.com/pypi/simple/"),
]


def step(text):
    print(f"\n>> {text}", flush=True)


def pip(*args, timeout=None):
    cmd = [sys.executable, "-m", "pip", *args, "--disable-pip-version-check"]
    try:
        return subprocess.run(cmd, timeout=timeout).returncode == 0
    except subprocess.TimeoutExpired:
        return False


def ensure_pip():
    if subprocess.run([sys.executable, "-m", "pip", "--version"], capture_output=True).returncode == 0:
        return True
    print("没有找到 pip，正在安装 pip…", flush=True)
    return subprocess.run([sys.executable, "-m", "ensurepip", "--upgrade"]).returncode == 0


def install_requirements():
    for name, url in INDEXES:
        print(f"尝试从{name}安装…", flush=True)
        args = ["install", "-r", str(REQUIREMENTS), "--timeout", "20"]
        if url:
            args += ["-i", url, "--trusted-host", url.split("/")[2]]
        if pip(*args, timeout=300):
            return True
        print(f"{name}安装失败，换下一个。", flush=True)
    return False


def check_imports():
    try:
        import openpyxl  # noqa: F401  (just installed, may not be importable until the path refreshes)
    except ImportError:
        return subprocess.run([sys.executable, "-c", "import openpyxl"], capture_output=True).returncode == 0
    return True


def desktop_shortcut():
    """Create 邮件助手.lnk on the desktop (OneDrive-redirected desktops included)."""
    q = lambda p: str(p).replace("'", "''")  # PowerShell single-quoted string
    ps = (
        "$d = [Environment]::GetFolderPath('Desktop'); "
        "$s = (New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path $d '邮件助手.lnk')); "
        f"$s.TargetPath = '{q(LAUNCHER)}'; $s.WorkingDirectory = '{q(ROOT)}'; $s.Save()"
    )
    r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps], capture_output=True)
    return r.returncode == 0


def main():
    print("=" * 50)
    print(" 邮件助手 一键安装")
    print("=" * 50)

    step("检查 Python 版本")
    version = ".".join(map(str, sys.version_info[:3]))
    print(f"当前 Python {version}（{sys.executable}）")
    if sys.version_info < MIN_PYTHON:
        print(f"需要 Python {'.'.join(map(str, MIN_PYTHON))} 或以上，请到 https://www.python.org/downloads/windows/ 安装新版本后再运行。")
        return 1

    step("检查 pip")
    if not ensure_pip():
        print("pip 安装失败，请联系 IT 或重新安装 Python（安装时保持默认勾选的 pip）。")
        return 1

    step("安装依赖库（openpyxl，用于导入 Excel 名单）")
    if not install_requirements() or not check_imports():
        print("依赖库安装失败。可能是网络无法访问 Python 软件源，请检查网络或联系 IT。")
        print("说明：不装也能使用，只是不能导入 .xlsx 名单（CSV 名单不受影响）。")
        return 1

    if sys.platform == "win32":
        step("在桌面创建快捷方式")
        print("已创建桌面快捷方式“邮件助手”。" if desktop_shortcut() else "快捷方式创建失败，可直接双击文件夹里的“邮件助手.pyw”启动。")

    print("\n安装完成！以后双击桌面上的“邮件助手”（或文件夹里的“邮件助手.pyw”）即可使用。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
