import sqlite3
import json
from datetime import datetime
import re
from .config import DB_PATH, TIMEZONE


def _connect():
    # fix #19: 所有线程独立连接，并统一设置忙等待和 Row 工厂
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA busy_timeout=5000")
    conn.row_factory = sqlite3.Row
    return conn

# =========================
# 初始化数据库
# =========================
def init_db():
    conn = _connect()
    try:
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS targets (
                name TEXT PRIMARY KEY,
                target_date TEXT NOT NULL
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS archives (
                name TEXT PRIMARY KEY,
                target_date TEXT NOT NULL,
                archived_date TEXT NOT NULL
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        ''')
        # fix #21: 为日报语言建立持久化默认值
        cursor.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
            ("language", "zh"),
        )
        conn.commit()
    finally:
        conn.close()

# =========================
# 添加或更新当前目标
# =========================
def add_target(name, date_str):
    date_str = normalize_date(date_str)
    if not _valid_name(name) or not date_str:
        return False

    conn = _connect()
    cursor = conn.cursor()
    try:
        cursor.execute(
            # fix: 同名添加明确拒绝，避免 INSERT OR REPLACE 覆盖原日期
            "INSERT INTO targets (name, target_date) VALUES (?, ?)",
            (name, date_str)
        )
        conn.commit()
        return True
    except sqlite3.Error:
        return False
    finally:
        conn.close()

# =========================
# 【新增】修改目标（支持改名 + 改日期）
# =========================
def update_target(old_name: str, new_name: str = None, new_date: str = None):
    """修改名称和/或日期。如果只改其中一项，另一项保持不变"""
    if new_name is None:
        new_name = old_name
    conn = _connect()
    cursor = conn.cursor()
    try:
        # fix: 同一事务内读取、检查冲突并更新，避免跨连接 TOCTOU
        conn.execute("BEGIN IMMEDIATE")
        cursor.execute("SELECT target_date FROM targets WHERE name = ?", (old_name,))
        row = cursor.fetchone()
        if not row:
            conn.rollback()
            return False
        if new_date is None:
            new_date = row[0]
        else:
            new_date = normalize_date(new_date)
            if not new_date:
                conn.rollback()
                return False
        if new_name != old_name:
            if not _valid_name(new_name):
                conn.rollback()
                return False
            cursor.execute("SELECT 1 FROM targets WHERE name = ?", (new_name,))
            if cursor.fetchone():
                conn.rollback()
                return False
        cursor.execute(
            "UPDATE targets SET name = ?, target_date = ? WHERE name = ?",
            (new_name, new_date, old_name),
        )
        if cursor.rowcount != 1:
            conn.rollback()
            return False
        conn.commit()
        return True
    except sqlite3.Error:
        conn.rollback()
        return False
    finally:
        conn.close()

# =========================
# 获取所有当前目标
# =========================
def load_targets():
    targets = {}
    conn = _connect()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT name, target_date FROM targets")
        rows = cursor.fetchall()
        for row in rows:
            name, date_str = row["name"], row["target_date"]
            try:
                target_date = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=TIMEZONE)
                targets[name] = target_date
            except (TypeError, ValueError) as error:
                # fix #18: 坏日期需可观察，避免读取时静默丢失
                print(f"db warning: skipped target {name!r} with invalid date ({error})")
    finally:
        conn.close()
    return targets

# =========================
# 归档目标
# =========================
def archive_target(name):
    conn = _connect()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT target_date FROM targets WHERE name = ?", (name,))
        row = cursor.fetchone()
        if not row:
            return False
        
        date_str = row[0]
        archived_date = datetime.now(TIMEZONE).strftime("%Y-%m-%d %H:%M")

        # fix: 归档同名时明确失败，避免覆盖已有归档
        cursor.execute(
            "INSERT INTO archives (name, target_date, archived_date) VALUES (?, ?, ?)",
            (name, date_str, archived_date)
        )
        cursor.execute("DELETE FROM targets WHERE name = ?", (name,))
        conn.commit()
        return True
    except sqlite3.Error:
        conn.rollback()
        return False
    finally:
        conn.close()

# =========================
# 获取所有已归档目标
# =========================
def load_archives():
    archives = {}
    conn = _connect()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT name, target_date FROM archives ORDER BY archived_date DESC")
        rows = cursor.fetchall()
        for row in rows:
            name, date_str = row["name"], row["target_date"]
            try:
                target_date = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=TIMEZONE)
                archives[name] = target_date
            except (TypeError, ValueError) as error:
                # fix #18: 坏归档日期需记录名称和原因后再跳过
                print(f"db warning: skipped archive {name!r} with invalid date ({error})")
    finally:
        conn.close()
    return archives

# =========================
# 导出/导入全部数据
# =========================
def export_all():
    return {
        "targets": {name: dt.strftime("%Y-%m-%d") for name, dt in load_targets().items()},
        "archives": {name: dt.strftime("%Y-%m-%d") for name, dt in load_archives().items()}
    }

def import_all(data: dict):
    result = {
        "targets_imported": 0,
        "archives_imported": 0,
        "skipped": 0,
        "conflicts": 0,
    }
    if not validate_import_data(data):
        return result
    # fix: 保留旧版“名称到日期”备份格式，同时限制现代格式的顶层字段
    if "targets" in data or "archives" in data:
        targets = data.get("targets", {})
        archives = data.get("archives", {})
    else:
        targets, archives = data, {}
    records = [("target", name, value) for name, value in targets.items()]
    records += [("archive", name, value) for name, value in archives.items()]
    if len(records) > 1000:
        return result

    normalized = []
    for kind, name, value in records:
        if not _valid_name(name):
            return result
        date_str = normalize_date(value)
        if not date_str:
            return result
        normalized.append((kind, name, date_str))

    conn = _connect()
    cursor = conn.cursor()
    try:
        conn.execute("BEGIN IMMEDIATE")
        for kind, name, date_str in normalized:
            table = "targets" if kind == "target" else "archives"
            cursor.execute(f"SELECT 1 FROM {table} WHERE name = ?", (name,))
            if cursor.fetchone():
                result["conflicts"] += 1
                continue
            if kind == "target":
                cursor.execute("INSERT INTO targets (name, target_date) VALUES (?, ?)", (name, date_str))
                result["targets_imported"] += 1
            else:
                archived_date = datetime.now(TIMEZONE).strftime("%Y-%m-%d %H:%M")
                cursor.execute(
                    "INSERT INTO archives (name, target_date, archived_date) VALUES (?, ?, ?)",
                    (name, date_str, archived_date),
                )
                result["archives_imported"] += 1
        conn.commit()
        return result
    except sqlite3.Error:
        conn.rollback()
        # fix: 任一写入异常时整体回滚，避免留下部分导入数据
        return {"targets_imported": 0, "archives_imported": 0, "skipped": len(normalized), "conflicts": 0}
    finally:
        conn.close()

# =========================
# 设置推送时间
# =========================
def set_push_time(time_str):
    # fix: 数据库层严格校验并规范化 HH:MM，阻止非法设置进入调度器
    try:
        candidate = str(time_str).strip()
        if not re.fullmatch(r"\d{2}:\d{2}", candidate):
            return False
        normalized_time = datetime.strptime(candidate, "%H:%M").strftime("%H:%M")
    except (TypeError, ValueError):
        return False
    conn = _connect()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
            ("push_time", normalized_time)
        )
        conn.commit()
        return True
    finally:
        conn.close()

def get_push_time():
    conn = _connect()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM settings WHERE key = 'push_time'")
        row = cursor.fetchone()
        return row["value"] if row else "09:00"
    finally:
        conn.close()


def set_language(language):
    # fix #21: 仅持久化支持的日报语言
    if language not in {"zh", "en"}:
        return False
    conn = _connect()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
            ("language", language),
        )
        conn.commit()
        return True
    finally:
        conn.close()


def get_language():
    # fix #21: 定时日报读取数据库中的语言设置
    conn = _connect()
    try:
        row = conn.execute("SELECT value FROM settings WHERE key = 'language'").fetchone()
        return row["value"] if row and row["value"] in {"zh", "en"} else "zh"
    finally:
        conn.close()


def _valid_name(name):
    return (
        isinstance(name, str)
        and bool(name.strip())
        and len(name) <= 100
        and not any(ord(char) < 32 for char in name)
    )


def validate_import_data(data):
    """校验导入结构，供 Telegram 层返回固定错误提示。"""
    if not isinstance(data, dict):
        return False
    if "targets" in data or "archives" in data:
        if set(data) - {"targets", "archives"}:
            return False
        targets = data.get("targets", {})
        archives = data.get("archives", {})
    else:
        targets, archives = data, {}
    if not isinstance(targets, dict) or not isinstance(archives, dict):
        return False
    if len(targets) + len(archives) > 1000:
        return False
    for records in (targets, archives):
        for name, date_str in records.items():
            if not _valid_name(name) or not isinstance(date_str, str) or not normalize_date(date_str):
                return False
    return True

# =========================
# 日期规范化
# =========================
def normalize_date(date_str):
    try:
        cleaned = str(date_str).replace('/', '-').replace(' ', '')
        if len(cleaned) == 8 and cleaned.isdigit():
            cleaned = f"{cleaned[:4]}-{cleaned[4:6]}-{cleaned[6:]}"
        dt = datetime.strptime(cleaned, "%Y-%m-%d")
        return dt.strftime("%Y-%m-%d")
    except:
        return None
