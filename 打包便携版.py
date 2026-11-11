"""在 Windows 上生成「邮件助手」免装 Python 的便携版。

用法：双击 打包便携版.bat（或在本文件夹运行 python 打包便携版.py）。
产物：dist/邮件助手_便携版/           —— 解压即用的文件夹
      dist/邮件助手_便携版.zip        —— 发给同事的压缩包

原理：把 Python 官方的“嵌入式（embeddable）”运行时和 openpyxl 一起打进包里，
同事拿到后不用安装 Python、不用联网装库，双击“邮件助手”即可使用。
"""

import os
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

# 打便携包用的 Python 版本（程序要求 3.11+）。换版本时改这一行即可。
PY_VERSION = "3.12.7"
EMBED_NAME = f"python-{PY_VERSION}-embed-amd64.zip"
# 官方源在前，国内镜像兜底（公司网络连不上官网时自动切换）。
EMBED_URLS = [
    f"https://www.python.org/ftp/python/{PY_VERSION}/{EMBED_NAME}",
    f"https://mirrors.huaweicloud.com/python/{PY_VERSION}/{EMBED_NAME}",
    f"https://registry.npmmirror.com/-/binary/python/{PY_VERSION}/{EMBED_NAME}",
]

ROOT = Path(__file__).resolve().parent
DIST = ROOT / "dist"
OUT = DIST / "邮件助手_便携版"
CACHE = DIST / "_cache"
# 随程序一起分发的内容（不含 userdata/backups 等个人数据）。
APP_ITEMS = ["mailer", "signatures", "config", "jobs", "README.md"]
DEPS = ["openpyxl>=3.1"]

LAUNCHER_VBS = (
    'Set fso = CreateObject("Scripting.FileSystemObject")\r\n'
    'base = fso.GetParentFolderName(WScript.ScriptFullName)\r\n'
    'Set sh = CreateObject("WScript.Shell")\r\n'
    'sh.CurrentDirectory = base\r\n'
    'sh.Run """" & base & "\\python\\pythonw.exe"" -m mailer.webapp", 0, False\r\n'
)

README_TXT = (
    "邮件助手 · 便携版（免装 Python）\r\n"
    "================================\r\n\r\n"
    "使用方法：\r\n"
    "1. 把本文件夹整个解压到任意位置（建议放到 D 盘或桌面，不要放在 C 盘的\r\n"
    "   “Program Files”里，那里没有写入权限）。\r\n"
    "2. 双击“邮件助手”启动，浏览器会自动打开操作界面。\r\n"
    "3. 第一次使用：在“账号设置”登录公司邮箱（填邮箱和授权码）。\r\n\r\n"
    "说明：\r\n"
    "- 不需要安装 Python，Python 已经打包在 python 文件夹里。\r\n"
    "- 你的名单、模板、发送记录都保存在本文件夹的 userdata 里，换电脑时\r\n"
    "  连同整个文件夹一起拷走即可。\r\n"
    "- 升级新版本：保留自己的 userdata、backups 文件夹，替换其余文件。\r\n"
)


def log(msg):
    print(f"\n>> {msg}", flush=True)


def download_embed():
    CACHE.mkdir(parents=True, exist_ok=True)
    cached = CACHE / EMBED_NAME
    if cached.is_file() and cached.stat().st_size > 1_000_000:
        print(f"已有缓存：{cached.name}")
        return cached
    for url in EMBED_URLS:
        print(f"下载 {url} …", flush=True)
        try:
            with urllib.request.urlopen(url, timeout=60) as r:
                data = r.read()
            if len(data) < 1_000_000:
                raise ValueError("文件太小，可能不是正确的安装包")
            cached.write_bytes(data)
            print(f"下载完成：{len(data) // 1024} KB")
            return cached
        except Exception as e:
            print(f"失败：{e}")
    raise SystemExit("无法下载便携版 Python，请检查网络后重试。")


def extract_python(embed_zip):
    py_dir = OUT / "python"
    py_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(embed_zip) as z:
        z.extractall(py_dir)
    # 打开 ._pth，让 Python 能找到程序目录和 libs（依赖库）目录。
    pth = next(py_dir.glob("python*._pth"), None)
    if pth is None:
        raise SystemExit("嵌入式包里找不到 ._pth 文件，打包中止。")
    lines = pth.read_text(encoding="utf-8").splitlines()
    kept = [ln for ln in lines if ln.strip() and not ln.strip().startswith("#")]
    for extra in ("..", r"..\libs"):
        if extra not in kept:
            kept.append(extra)
    if "import site" not in kept:
        kept.append("import site")
    pth.write_text("\n".join(kept) + "\n", encoding="utf-8")
    print(f"已配置 {pth.name}")
    return py_dir


def install_deps():
    libs = OUT / "libs"
    libs.mkdir(parents=True, exist_ok=True)
    # 用“打包这台电脑”的 Python 把纯 Python 的库装进 libs/（openpyxl 无需编译）。
    cmd = [sys.executable, "-m", "pip", "install", "--target", str(libs),
           "--no-compile", "--disable-pip-version-check", *DEPS]
    if subprocess.run(cmd).returncode != 0:
        raise SystemExit("安装 openpyxl 失败，请确认这台电脑能联网安装 pip 包。")


def copy_app():
    for item in APP_ITEMS:
        src = ROOT / item
        if not src.exists():
            print(f"（跳过不存在的 {item}）")
            continue
        dst = OUT / item
        if src.is_dir():
            shutil.copytree(src, dst, dirs_exist_ok=True,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        else:
            shutil.copy2(src, dst)
    (OUT / "邮件助手.vbs").write_text(LAUNCHER_VBS, encoding="utf-8")
    (OUT / "使用说明.txt").write_text(README_TXT, encoding="utf-8")


def make_zip():
    archive = DIST / "邮件助手_便携版.zip"
    if archive.exists():
        archive.unlink()
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as z:
        for path in sorted(OUT.rglob("*")):
            if path.is_file():
                z.write(path, Path("邮件助手_便携版") / path.relative_to(OUT))
    return archive


def main():
    print("=" * 50)
    print(" 邮件助手 · 生成便携版")
    print("=" * 50)
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True, exist_ok=True)

    log("1/5 下载便携版 Python")
    embed = download_embed()
    log("2/5 解压并配置 Python")
    extract_python(embed)
    log("3/5 安装依赖库 openpyxl")
    install_deps()
    log("4/5 复制程序文件")
    copy_app()
    log("5/5 打包成 zip")
    archive = make_zip()

    print("\n完成！")
    print(f"文件夹：{OUT}")
    print(f"压缩包：{archive}  （把这个发给同事）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
