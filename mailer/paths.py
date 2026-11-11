"""Where the user's data lives.

Data (userdata/ and backups/) is kept OUTSIDE the program folder — by default in
「文档\\邮件助手数据」— so updating the program means replacing its folder without
touching anyone's accounts, templates or send history. The chosen location is
remembered in a small file in the user's home folder, so it survives replacing
the program folder, and the user can move it from the UI.
"""

import json
import shutil
from pathlib import Path

CONFIG_FILE = Path.home() / ".邮件助手.json"
_PROGRAM_DIR = Path(__file__).resolve().parent.parent  # old layout kept data in here
LEGACY_USERDATA = _PROGRAM_DIR / "userdata"
LEGACY_BACKUPS = _PROGRAM_DIR / "backups"
_FOLDER_NAME = "邮件助手数据"


def _documents():
    home = Path.home()
    for c in (home / "Documents", home / "OneDrive" / "Documents", home / "OneDrive" / "文档", home / "文档"):
        if c.is_dir():
            return c
    return home


def default_data_home():
    return _documents() / _FOLDER_NAME


def _read_config():
    try:
        return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _write_config(cfg):
    try:
        CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


def data_home():
    """The configured data folder (may not exist yet)."""
    cfg = _read_config()
    return Path(cfg["data_home"]) if cfg.get("data_home") else default_data_home()


def _migrate(src_home, dst_home):
    """Move userdata/ and backups/ from one data home to another (best effort)."""
    for name in ("userdata", "backups"):
        src, dst = src_home / name, dst_home / name
        if src.exists() and not dst.exists():
            try:
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(src), str(dst))
            except OSError as e:
                print(f"迁移数据失败（{src} -> {dst}）：{e}", flush=True)


def prepare_data_home():
    """Resolve the data folder, run any one-time migration, and return it with
    userdata/ and backups/ present. Call this once at startup, before anything
    opens a file under it."""
    cfg = _read_config()
    home = Path(cfg["data_home"]) if cfg.get("data_home") else default_data_home()
    home.mkdir(parents=True, exist_ok=True)

    if not (home / "userdata").exists():
        move_from = cfg.get("move_from")
        if move_from and Path(move_from) != home:
            _migrate(Path(move_from), home)              # a move requested from the UI
        if not (home / "userdata").exists() and LEGACY_USERDATA.exists():
            _migrate(_PROGRAM_DIR, home)                 # first run after upgrading from the old layout

    (home / "userdata").mkdir(parents=True, exist_ok=True)
    (home / "backups").mkdir(parents=True, exist_ok=True)
    if cfg.get("move_from") or not cfg.get("data_home"):
        cfg.pop("move_from", None)
        cfg["data_home"] = str(home)
        _write_config(cfg)
    return home


def set_data_home(new_path):
    """Record a new data location and that the data should move there on next
    start. Returns the resolved Path. Raises OSError if it is not writable."""
    new = Path(new_path).expanduser()
    if not str(new).strip():
        raise OSError("位置不能为空")
    new.mkdir(parents=True, exist_ok=True)
    probe = new / ".write_test"
    probe.write_text("ok", encoding="utf-8")             # raises OSError if not writable
    probe.unlink()
    cfg = _read_config()
    old = cfg.get("data_home") or str(default_data_home())
    if Path(old) != new:
        cfg["move_from"] = old
    cfg["data_home"] = str(new)
    _write_config(cfg)
    return new
