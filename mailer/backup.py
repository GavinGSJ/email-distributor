"""Export / restore the user's data as one .zip file.

backup.json                 manifest: what is inside, when, from which account
data/account.json           account settings WITHOUT the password
data/<file>.json            signature, templates, lists, groups, draft
data/uploads/<id>/<file>    only the uploads still referenced (attachments, logo)
data/history/<batch>/...    send records

Restoring never deletes send records, and only files matching the layout above are
ever extracted, so a damaged or hand-crafted zip cannot write elsewhere."""

import json
import re
import zipfile
from datetime import date, datetime
from pathlib import Path

from .errors import ConfigError

FORMAT = "mailer-backup"
VERSION = 1
MAX_TOTAL_BYTES = 2 * 1024**3

# key -> (label, file in userdata, kind) ; kind: "single" (one object) | "named" (name -> item) | "dir"
ITEMS = {
    "account": ("账号设置（不含密码）", "account.json", "single"),
    "signature": ("签名", "signature.json", "single"),
    "templates": ("邮件模板", "templates.json", "named"),
    "lists": ("收件人名单", "recipient_lists.json", "named"),
    "groups": ("抄送/密送组合", "email_groups.json", "named"),
    "draft": ("草稿", "draft.json", "single"),
    "history": ("发送记录", "history", "dir"),
}
_UPLOAD = re.compile(r"^data/uploads/([0-9a-f]{32})/([^/\\]+)$")
_HISTORY = re.compile(r"^data/history/(\d{8}-\d{6}-[0-9a-f]{4})/(batch\.json|\d{3}\.eml|attachments/[^/\\]+)$")
_DATA_FILES = {f"data/{file}": key for key, (_, file, kind) in ITEMS.items() if kind != "dir"}


# -- export ---------------------------------------------------------------------
def export(root, dest, items=None):
    """Write a backup of userdata folder `root` to `dest`; returns the manifest."""
    root, dest = Path(root), Path(dest)
    items = [k for k in (items or ITEMS) if k in ITEMS]
    if not items:
        raise ConfigError("请至少选择一项要导出的内容")
    dest.parent.mkdir(parents=True, exist_ok=True)
    counts, upload_ids = {}, set()
    tmp = dest.with_suffix(".part")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        for key in items:
            _, file, kind = ITEMS[key]
            if kind == "dir":
                folder = root / file
                batches = sorted(p for p in folder.iterdir() if p.is_dir()) if folder.is_dir() else []
                for batch in batches:
                    for f in batch.rglob("*"):
                        if f.is_file() and not f.name.endswith(".tmp"):
                            z.write(f, f"data/{file}/{f.relative_to(folder).as_posix()}")
                counts[key] = len(batches)
                continue
            data = _read(root / file)
            if data is None:
                continue
            if key == "account":
                data = {k: v for k, v in data.items() if k != "password"}
            z.writestr(f"data/{file}", json.dumps(data, ensure_ascii=False, indent=2))
            counts[key] = len(data) if kind == "named" else 1
            upload_ids |= _referenced_uploads(key, data)
        for uid in sorted(upload_ids):
            folder = root / "uploads" / uid
            for f in folder.iterdir() if folder.is_dir() else []:
                if f.is_file():
                    z.write(f, f"data/uploads/{uid}/{f.name}")
        account = _read(root / "account.json") or {}
        manifest = {
            "format": FORMAT,
            "version": VERSION,
            "created": f"{datetime.now():%Y-%m-%d %H:%M:%S}",
            "account": account.get("email", ""),
            "items": {k: {"label": ITEMS[k][0], "count": n} for k, n in counts.items()},
        }
        z.writestr("backup.json", json.dumps(manifest, ensure_ascii=False, indent=2))
    tmp.replace(dest)
    return manifest


def _referenced_uploads(key, data):
    ids = set()
    if key == "signature" and data.get("logo"):
        ids.add(data["logo"].get("id", ""))
    elif key == "draft":
        ids |= {a.get("id", "") for a in data.get("attachments") or []}
    elif key == "templates":
        for t in data.values():
            ids |= {a.get("id", "") for a in t.get("attachments") or []}
    return {i for i in ids if re.fullmatch(r"[0-9a-f]{32}", i or "")}


def _read(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return None


# -- inspect & restore ----------------------------------------------------------
def inspect(path):
    """Manifest of a backup file, after checking it really is one."""
    try:
        with zipfile.ZipFile(path) as z:
            manifest = json.loads(z.read("backup.json"))
            total = sum(i.file_size for i in z.infolist())
    except (zipfile.BadZipFile, KeyError, ValueError, OSError):
        raise ConfigError("这不是邮件助手的备份文件，或文件已损坏")
    if manifest.get("format") != FORMAT:
        raise ConfigError("这不是邮件助手的备份文件")
    if manifest.get("version", 0) > VERSION:
        raise ConfigError("这个备份来自更新版本的邮件助手，请先更新程序")
    if total > MAX_TOTAL_BYTES:
        raise ConfigError("备份文件过大，无法导入")
    return manifest


def restore(root, path, items, mode="merge"):
    """Bring the chosen items from a backup into userdata folder `root`.

    merge:   keep everything already here; imported entries whose name is taken
             are added as "名称（导入）"; account/signature/draft only fill in if empty.
    replace: imported items overwrite the current ones (password and send records
             are always kept).
    Returns {key: description} for a summary dialog."""
    if mode not in ("merge", "replace"):
        raise ConfigError("未知的恢复方式")
    inspect(path)
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)  # e.g. a fresh copy of the program
    summary = {}
    with zipfile.ZipFile(path) as z:
        names = set(z.namelist())
        for key in items:
            if key not in ITEMS:
                continue
            label, file, kind = ITEMS[key]
            if kind == "dir":
                summary[key] = _restore_history(z, names, root)
                continue
            if f"data/{file}" not in names:
                continue
            data = json.loads(z.read(f"data/{file}"))
            if kind == "named":
                summary[key] = _restore_named(root / file, data, mode)
            else:
                summary[key] = _restore_single(key, root / file, data, mode)
            _restore_uploads(z, names, root, _referenced_uploads(key, data))
    return summary


def _restore_named(path, incoming, mode):
    current = (_read(path) or {}) if mode == "merge" else {}
    added = renamed = same = 0
    for name, item in incoming.items():
        if name not in current:
            current[name], added = item, added + 1
        elif current[name] == item:
            same += 1
        else:
            new, i = f"{name}（导入）", 2
            while new in current:
                new, i = f"{name}（导入{i}）", i + 1
            current[new], renamed = item, renamed + 1
    _write(path, current)
    parts = [f"新增 {added} 个"] + ([f"重名改为“（导入）” {renamed} 个"] if renamed else []) + ([f"已存在相同的 {same} 个"] if same else [])
    return "，".join(parts) if mode == "merge" else f"已替换为备份中的 {len(incoming)} 个"


def _restore_single(key, path, incoming, mode):
    current = _read(path)
    if key == "account":
        keep_password = (current or {}).get("password", "") if (current or {}).get("email") == incoming.get("email") else ""
        if mode == "merge" and current:
            return "已有账号设置，保持不变"
        _write(path, {**incoming, "password": keep_password})
        return "已恢复" + ("" if keep_password else "，请到“账号设置”重新输入密码/授权码")
    empty = not current or (key == "signature" and not current.get("text") and not current.get("logo")) or (
        key == "draft" and not (current.get("subject") or current.get("body")))
    if mode == "merge" and not empty:
        return "已有内容，保持不变（如需覆盖请选“替换”）"
    _write(path, incoming)
    return "已恢复"


def _restore_uploads(z, names, root, ids):
    for name in names:
        m = _UPLOAD.match(name)
        if m and m.group(1) in ids:
            target = root / "uploads" / m.group(1) / m.group(2)
            if not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(z.read(name))


def _restore_history(z, names, root):
    added = set()
    existing = {p.name for p in (root / "history").iterdir()} if (root / "history").is_dir() else set()
    for name in sorted(names):
        m = _HISTORY.match(name)
        if not m or m.group(1) in existing:
            continue
        target = root / "history" / m.group(1) / m.group(2)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(z.read(name))
        added.add(m.group(1))
    skipped = len({m.group(1) for n in names if (m := _HISTORY.match(n))} - added)
    return f"新增 {len(added)} 批" + (f"，已存在 {skipped} 批" if skipped else "")


def _write(path, data):
    path = Path(path)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


# -- backup folder ----------------------------------------------------------------
class BackupFolder:
    """backups/            manual exports (the user copies these to a cloud drive)
    backups/自动备份/     daily snapshot + one before every restore, newest 7 kept
    backups/导入的备份/   zip files picked from elsewhere in the restore dialog"""

    AUTO = "自动备份"
    IMPORTED = "导入的备份"
    KEEP_AUTO = 7

    def __init__(self, folder, data_root):
        self.folder = Path(folder)
        self.auto = self.folder / self.AUTO
        self.data_root = Path(data_root)

    def export(self, items=None):
        dest = self.folder / f"邮件助手备份_{datetime.now():%Y%m%d-%H%M%S}.zip"
        manifest = export(self.data_root, dest, items)
        return {"name": dest.name, "path": str(dest), "size": dest.stat().st_size, "manifest": manifest}

    def snapshot(self, reason):
        dest = self.auto / f"{reason}_{datetime.now():%Y%m%d-%H%M%S}.zip"
        export(self.data_root, dest)
        for old in sorted(self.auto.glob("*.zip"), key=lambda p: p.stat().st_mtime, reverse=True)[self.KEEP_AUTO:]:
            old.unlink()
        return dest

    def daily_snapshot(self):
        """At most one automatic snapshot per day; nothing to back up yet -> skip."""
        if not any((self.data_root / f).exists() for _, f, _ in ITEMS.values()):
            return None
        today = f"{date.today():%Y%m%d}"
        if any(today in p.name for p in self.auto.glob("每日_*.zip")):
            return None
        return self.snapshot("每日")

    def listing(self):
        kinds = {self.folder: "manual", self.auto: "auto", self.folder / self.IMPORTED: "imported"}
        files = [f for d in kinds if d.is_dir() for f in d.glob("*.zip")]
        out = []
        for f in sorted(files, key=lambda p: p.stat().st_mtime, reverse=True):
            try:
                manifest = inspect(f)
            except ConfigError:
                continue
            out.append({
                "name": f.relative_to(self.folder).as_posix(),
                "kind": kinds[f.parent],
                "size": f.stat().st_size,
                "created": manifest["created"],
                "account": manifest.get("account", ""),
                "items": manifest["items"],
            })
        return out

    def resolve(self, name):
        """Path of a backup inside the folder; refuses anything that escapes it."""
        path = (self.folder / name).resolve()
        if self.folder.resolve() not in path.parents or path.suffix.lower() != ".zip" or not path.is_file():
            raise ConfigError("找不到这个备份文件")
        return path

    def save_uploaded(self, filename, data):
        name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", Path(filename).name) or "导入的备份.zip"
        if not name.lower().endswith(".zip"):
            raise ConfigError("请选择 .zip 格式的备份文件")
        dest = self.folder / self.IMPORTED / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        try:
            inspect(dest)
        except ConfigError:
            dest.unlink()
            raise
        return dest.relative_to(self.folder).as_posix()
