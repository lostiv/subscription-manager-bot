import requests
import json
import html
import re
import threading
import time
# feat: 使用标准库编码续费回调中的目标名称
from urllib.parse import quote, unquote
from datetime import datetime
from .db import (
    load_targets,
    update_target,
    archive_target,
    load_archives,
    export_all,
    import_all,
    normalize_date,
    validate_import_data,
    get_push_time,
    get_language,
    set_language,
    has_reminded,
    mark_reminded,
    renew_target,
)
from .config import BOT_TOKEN, TG_USER_ID, BASE_URL, TIMEZONE
# feat: 接入统一的提醒节点判定
from .utils import reminder_node_for

last_offset = 0
USER_MSG_TTL_SECONDS = 30
_pending_deletions = []
_pending_deletions_lock = threading.Lock()
_cleanup_started = False

user_state = {
    "pending_action": None,
    "pending_edit_target": None,
    "pending_import": False,
    "panel_message_id": None,
    "panel_state": None,
    "renew_name": None,
    "renew_months": None,
}


def _queue_user_message(update, now=None):
    """将已鉴权的文本用户消息加入定时清理队列。"""
    message = update.get("message", {})
    if not message.get("text") or message.get("message_id") is None:
        return
    if now is None:
        now = time.time()
    chat_id = message.get("chat", {}).get("id")
    with _pending_deletions_lock:
        _pending_deletions.append((now + USER_MSG_TTL_SECONDS, chat_id, message["message_id"]))


def _cleanup_sweep(now=None):
    """删除到期的用户消息，并移除所有已处理条目。"""
    if now is None:
        now = time.time()
    with _pending_deletions_lock:
        due = [item for item in _pending_deletions if now >= item[0]]
        _pending_deletions[:] = [item for item in _pending_deletions if now < item[0]]
    for _, chat_id, message_id in due:
        try:
            delete_message(chat_id, message_id)
        except Exception:
            pass
    return len(due)


def _cleanup_worker():
    while True:
        try:
            _cleanup_sweep()
            time.sleep(5)
        except Exception as error:
            print(f"⚠️ Cleanup warning: {type(error).__name__}")
            time.sleep(5)


def start_cleanup_worker():
    global _cleanup_started
    if _cleanup_started:
        return
    _cleanup_started = True
    thread = threading.Thread(target=_cleanup_worker)
    thread.daemon = True
    thread.start()

# ====================== 多语言字典 ======================
TRANSLATIONS = {
    "no_targets": {"en": "No targets currently", "zh": "当前没有任何目标"},
    "current_targets_title": {"en": "Current Targets", "zh": "当前目标"},
    "overdue": {"en": "Overdue", "zh": "逾期"},
    "expiring_soon": {"en": "Expiring Soon", "zh": "即将到期"},
    "medium_term": {"en": "Medium-term", "zh": "中期"},
    "long_term": {"en": "Long-term", "zh": "长期"},
    "overdue_str": {"en": "Overdue", "zh": "逾期"},
    "expiring_soon_str": {"en": "⏳ {days} days", "zh": "⏳ {days}天"},
    "normal_days_str": {"en": "{days} days", "zh": "{days}天"},

    "edit_button": {"en": "✏️ Edit Target", "zh": "✏️ 修改目标"},
    "archive_button": {"en": "📦 Archive Target", "zh": "📦 归档目标"},
    "refresh_button": {"en": "🔄 View All Targets", "zh": "🔄 查看所有目标"},
    "add_button": {"en": "➕ Add Target", "zh": "➕ 添加目标"},
    "export_button": {"en": "📤 Export All", "zh": "📤 导出全部"},
    "import_button": {"en": "📥 Import All", "zh": "📥 导入全部"},
    "set_time_button": {"en": "⏰ Set Daily Push Time", "zh": "⏰ 设置每日推送时间"},
    "renew_action_button": {"en": "🔄 Renewed (action)", "zh": "🔄 已续费"},

    "edit_prompt": {"en": "✏️ Please enter the <b>number</b> of the target to edit (e.g. 1 or 2...)", "zh": "✏️ 请输入要<b>修改</b>的目标序号（例如：1或2...）"},
    "archive_prompt": {"en": "📦 Please enter the <b>number</b> of the target to archive (enter <b>0</b> to view all archived; enter <b>1 or 2...</b> to archive)", "zh": "📦 请输入要<b>归档</b>的目标序号（输入 <b>0</b> 查看所有历史归档;输入<b>1或2...</b> 归档目标）"},
    "renew_select_prompt": {"en": "Pick target to renew (send its number):", "zh": "选择要续费的目标（发送编号）："},
    "add_target_prompt": {"en": "➕ Please enter: /addsub &lt;name&gt; &lt;date&gt;\nExample: /addsub XChat Registration 2026-04-25", "zh": "➕ 请输入：/addsub &lt;名称&gt; &lt;日期&gt;\n示例：/addsub XChat注册 2026-04-25"},
    "set_time_prompt": {"en": "Please enter the new push time in HH:MM format", "zh": "请输入新的推送时间，格式：HH:MM"},
    "export_success": {"en": "📤 <b>Full backup generated</b>\n\n<code>{json_str}</code>", "zh": "📤 <b>完整备份已生成</b>\n\n<code>{json_str}</code>"},
    "export_too_large": {"en": "❌ Export is too large to send", "zh": "❌ 导出数据过大，无法发送"},
    "import_prompt": {"en": "📥 Please paste the complete JSON you want to import", "zh": "📥 请直接粘贴你要导入的完整 JSON"},
    "import_invalid": {"en": "❌ Invalid import JSON or unsupported data", "zh": "❌ 导入 JSON 无效或数据不受支持"},
    "import_summary": {"en": "✅ Import complete: {targets} targets, {archives} archives, {conflicts} conflicts, {skipped} skipped", "zh": "✅ 导入完成：目标 {targets}，归档 {archives}，冲突 {conflicts}，跳过 {skipped}"},
    "push_time_failed": {"en": "❌ Invalid push time; use HH:MM", "zh": "❌ 推送时间无效，请使用 HH:MM 格式"},
    "start_welcome": {"en": "👋 <b>Telegram Target Bot</b>\n\nUse the buttons below to add/edit targets", "zh": "👋 <b>Telegram 目标机器人</b>\n\n添加/修改目标请使用下方按钮"},
    "edit_current": {"en": "✏️ Current: <b>{name}</b> ({date})\n\nPlease enter: new name (optional) new date (YYYY-MM-DD)\nExample: Netflix Family 2026-12-20\nOr just the date: 2026-12-20", "zh": "✏️ 当前：<b>{name}</b>（{date}）\n\n请输入：新名称（可选） 新日期（YYYY-MM-DD）\n示例：Netflix家庭 2026-12-20\n或只输日期：2026-12-20"},
    "edit_success": {"en": "✅ Edit successful!", "zh": "✅ 修改成功！"},
    "edit_failed": {"en": "❌ Edit failed", "zh": "❌ 修改失败"},
    "archive_success": {"en": "✅ Target archived: 「{name}」", "zh": "✅ 已归档 「{name}」"},
    "archive_failed": {"en": "❌ Archive failed", "zh": "❌ 归档失败"},
    "no_archived": {"en": "📦 No archived targets yet", "zh": "📦 暂无任何已归档目标"},
    "archived_history": {"en": "📦 <b>Archived History</b>\n\n", "zh": "📦 <b>历史已归档目标</b>\n\n"},
    "add_success": {"en": "✅ Added successfully!", "zh": "✅ 添加成功！"},
    "add_failed": {"en": "❌ Add failed, please check date format (YYYY-MM-DD)", "zh": "❌ 添加失败，请检查日期格式（YYYY-MM-DD）"},
    "format_error": {"en": "❌ Format error\nCorrect example: /addsub XChat Registration 2026-04-25", "zh": "❌ 格式错误\n正确示例：/addsub XChat Registration 2026-04-25"},
    "push_time_set": {"en": "✅ Push time has been set to <b>{time}</b>", "zh": "✅ 推送时间已设置为 <b>{time}</b>"},
    "import_success": {"en": "✅ Successfully imported {count} targets!", "zh": "✅ 成功导入 {count} 个目标！"},
    "json_error": {"en": "❌ JSON format error: {error}", "zh": "❌ JSON 格式错误：{error}"},
    "daily_report_title": {"en": "Daily Report", "zh": "每日报告"},
    "edit_format_error": {"en": "❌ Format error\nEnter a new name and date (YYYY-MM-DD), or just a date", "zh": "❌ 格式错误\n请输入新名称和日期（YYYY-MM-DD），或只输入日期"},
    "language_usage": {"en": "Usage: /language zh|en", "zh": "用法：/language zh|en"},
    "language_set": {"en": "✅ Daily report language set to English", "zh": "✅ 日报语言已设置为中文"},
    # feat: 增加多节点提醒和续费交互的中英文文案
    "reminder_due": {"en": "🔔 <b>{name}</b> expires in {days} days ({date})", "zh": "🔔 <b>{name}</b> 还有 {days} 天到期（{date}）"},
    "reminder_overdue": {"en": "⚠️ <b>{name}</b> overdue by {days} days (original expiry {date})", "zh": "⚠️ <b>{name}</b> 已逾期 {days} 天（原到期 {date}）"},
    "renew_button": {"en": "✅ Renewed", "zh": "✅ 已续费"},
    "renew_period_prompt": {"en": "⏳ {name} renewal extension, choose a period:", "zh": "⏳ {name} 续费顺延，选择周期："},
    "renew_opt_1m": {"en": "+1 month", "zh": "+1 个月"},
    "renew_opt_3m": {"en": "+1 quarter", "zh": "+1 季度"},
    "renew_opt_12m": {"en": "+1 year", "zh": "+1 年"},
    "renew_success": {"en": "✅ {name} new expiry: {date}", "zh": "✅ {name} 新到期日：{date}"},
    "renew_failed": {"en": "❌ Renewal failed (target does not exist or has been archived)", "zh": "❌ 续费失败（目标不存在或已被归档）"},
    "renew_usage": {"en": "Usage: /renew <name> (or use the reminder button)", "zh": "用法 /renew <名称>（或从提醒按钮进入）"},
    "renew_mode_prompt": {"en": "⏳ {name} renew {months} month(s), choose start date:", "zh": "⏳ {name} 续费 {months} 个月，选择起始日期："},
    "renew_mode_today": {"en": "📅 From today ({date})", "zh": "📅 从今天起算（{date}）"},
    "renew_mode_original": {"en": "📅 Extend from expiry ({date})", "zh": "📅 从原到期顺延（{date}）"},
    "back_button": {"en": "⬅️ Back", "zh": "⬅️ 返回"},
    "cancel_button": {"en": "❌ Cancel", "zh": "❌ 取消"},
    "cancelled": {"en": "✅ Cancelled", "zh": "✅ 已取消"},
    "close_button": {"en": "❌ Close", "zh": "❌ 关闭"},
    "closed_panel": {"en": "📴 Panel closed.\nSend /start or /subs to reopen.", "zh": "📴 面板已关闭。\n需要继续时，发送 /start 或 /subs 重新打开。"},
}

def send_msg(text, reply_markup=None):
    """发送消息（核心函数，已置于最上方）"""
    url = f"{BASE_URL}sendMessage"
    payload = {"chat_id": TG_USER_ID, "text": text, "parse_mode": "HTML"}
    if reply_markup:
        payload["reply_markup"] = json.dumps(reply_markup)
    try:
        response = requests.post(url, data=payload, timeout=10)
        if response.status_code != 200:
            # fix #2: 日志只记录状态码，避免泄露请求上下文或凭据
            print(f"❌ Send failed: HTTP {response.status_code}")
            return None
        if response.json().get("ok") is False:
            print("❌ Send failed: Telegram API error")
            return None
        else:
            print("✅ Message sent successfully")
            result = response.json().get("result")
            if isinstance(result, list) and result:
                message_id = result[0].get("message_id")
            elif isinstance(result, dict):
                message_id = result.get("message_id")
            else:
                message_id = None
            return int(message_id) if message_id is not None else None
    except Exception as e:
        print(f"❌ Send error: {type(e).__name__}")
        return None


def delete_message(chat_id, message_id):
    """尽力删除面板消息，失败时静默返回。"""
    try:
        requests.post(
            f"{BASE_URL}deleteMessage",
            data={"chat_id": chat_id, "message_id": message_id},
            timeout=10,
        )
    except Exception:
        pass


def edit_msg(chat_id, message_id, text, reply_markup=None, remove_keyboard=False):
    """原地编辑消息，失败时由调用方回退发送新消息。"""
    payload = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
        "parse_mode": "HTML",
    }
    if reply_markup is not None:
        payload["reply_markup"] = json.dumps(reply_markup)
    elif remove_keyboard:
        payload["reply_markup"] = json.dumps({"inline_keyboard": []})
    try:
        response = requests.post(f"{BASE_URL}editMessageText", data=payload, timeout=10)
        return response.status_code == 200 and response.json().get("ok", True) is not False
    except Exception:
        return False


def panel_send_or_edit(text, keyboard=None, new_state=None, remove_keyboard=False):
    """在单一面板消息上编辑，编辑失败时删除旧消息并重新发送。"""
    try:
        mid = user_state.get("panel_message_id")
        if mid is not None:
            if edit_msg(TG_USER_ID, mid, text, keyboard, remove_keyboard):
                user_state["panel_state"] = new_state
                return True, mid
            delete_message(TG_USER_ID, mid)
            user_state["panel_message_id"] = None
        new_mid = send_msg(text, keyboard)
        if new_mid is None:
            return False, None
        user_state["panel_message_id"] = new_mid
        user_state["panel_state"] = new_state
        return True, new_mid
    except Exception:
        return False, None


def send_export(json_str, lang):
    """以文档发送备份，避免 Telegram 文本消息长度限制。"""
    if len(json_str.encode("utf-8")) > 10 * 1024 * 1024:
        return send_msg(get_text("export_too_large", lang), generate_inline_buttons(lang))
    url = f"{BASE_URL}sendDocument"
    payload = {"chat_id": TG_USER_ID, "reply_markup": json.dumps(generate_inline_buttons(lang))}
    try:
        response = requests.post(
            url,
            data=payload,
            files={"document": ("subscriptions.json", json_str.encode("utf-8"), "application/json")},
            timeout=10,
        )
        if response.status_code != 200:
            # fix: 导出改为文档发送，并限制文件大小，避免超长消息失败
            print(f"❌ Export failed: HTTP {response.status_code}")
            return False
        return True
    except Exception as e:
        print(f"❌ Export error: {type(e).__name__}")
        return False


def is_authorized_update(update):
    """只允许配置的用户和聊天触发业务操作。"""
    if "message" in update:
        message = update.get("message", {})
        chat_id = message.get("chat", {}).get("id")
        user_id = message.get("from", {}).get("id")
    elif "callback_query" in update:
        callback = update.get("callback_query", {})
        chat_id = callback.get("message", {}).get("chat", {}).get("id")
        user_id = callback.get("from", {}).get("id")
    else:
        return False
    try:
        # fix: 统一校验 chat.id/from.id，且使用整数比较防止字符串绕过
        return int(chat_id) == TG_USER_ID and int(user_id) == TG_USER_ID
    except (TypeError, ValueError):
        return False

def get_user_lang(update):
    """自动获取用户 Telegram 语言"""
    if "message" in update and "from" in update["message"]:
        lang = update["message"]["from"].get("language_code", "en")
    elif "callback_query" in update and "from" in update["callback_query"]:
        lang = update["callback_query"]["from"].get("language_code", "en")
    else:
        lang = "en"
    return "zh" if lang.startswith("zh") else "en"

def get_text(key, lang="en", **kwargs):
    lang = "zh" if lang.startswith("zh") else "en"
    text = TRANSLATIONS.get(key, {}).get(lang, TRANSLATIONS.get(key, {}).get("en", key))
    if kwargs:
        text = text.format(**kwargs)
    return text


def _parse_command(text):
    # fix #17: 只按完整命令 token 分派，并兼容 /command@botname
    parts = text.split(maxsplit=1)
    if not parts or not parts[0].startswith("/"):
        return None, ""
    command = parts[0].split("@", 1)[0].lower()
    return command, parts[1] if len(parts) == 2 else ""


def _parse_name_date(text):
    # fix #16: 从输入末尾识别日期，名称保留前面的全部内容
    match = re.fullmatch(r"(.+?)\s+(\d{4}-\d{2}-\d{2})", text.strip())
    if not match:
        return None
    name = match.group(1).strip()
    date_str = normalize_date(match.group(2))
    if not name or not date_str:
        return None
    return name, date_str

def generate_inline_buttons(lang="en"):
    keyboard = {
        "inline_keyboard": [
            [
                {"text": get_text("edit_button", lang), "callback_data": "action_edit"},
                {"text": get_text("archive_button", lang), "callback_data": "action_archive"},
                {"text": get_text("renew_action_button", lang), "callback_data": "action_renew"},
            ],
            [
                {"text": get_text("refresh_button", lang), "callback_data": "show_subscriptions"},
                {"text": get_text("add_button", lang), "callback_data": "add_target"},
            ],
            [
                {"text": get_text("export_button", lang), "callback_data": "export_data"},
                {"text": get_text("import_button", lang), "callback_data": "import_data"},
            ],
            [
                {"text": get_text("set_time_button", lang), "callback_data": "set_time"},
            ],
            [
                {"text": get_text("close_button", lang), "callback_data": "close_menu"},
            ]
        ]
    }
    return keyboard

def format_numbered_targets(targets, lang="en"):
    if not targets:
        return get_text("no_targets", lang)
    
    message = f"                  <b>{get_text('current_targets_title', lang)}</b>\n\n"
    
    # fix #6: 倒计时日期使用统一的带时区本地日期
    now_date = datetime.now(TIMEZONE).date()
    
    categorized = {
        "Overdue":       {"emoji": "⚠️", "key": "overdue"},
        "Expiring Soon": {"emoji": "🚨", "key": "expiring_soon"},
        "Medium-term":   {"emoji": "📊", "key": "medium_term"},
        "Long-term":     {"emoji": "📦", "key": "long_term"}
    }
    
    items = []
    for name, target_time in targets.items():
        days = (target_time.date() - now_date).days
        if days < 0:
            category = "Overdue"
        elif days <= 30:
            category = "Expiring Soon"
        elif days <= 365:
            category = "Medium-term"
        else:
            category = "Long-term"
        items.append({"name": name, "days": days, "category": category})
    
    items.sort(key=lambda x: x["days"])
    
    idx = 1
    for cat_key, cat_data in categorized.items():
        cat_items = [item for item in items if item["category"] == cat_key]
        if not cat_items:
            continue
        message += f"{cat_data['emoji']} <b>{get_text(cat_data['key'], lang)}</b>\n\n"
        
        for item in cat_items:
            if item["category"] == "Overdue":
                day_str = get_text("overdue_str", lang)
            elif item["category"] == "Expiring Soon":
                day_str = get_text("expiring_soon_str", lang, days=item["days"])
            else:
                day_str = get_text("normal_days_str", lang, days=item["days"])
            # fix #11: 用户输入的目标名称先 HTML 转义，避免破坏消息实体
            message += f"{idx}. {html.escape(item['name'])}:  <b>{day_str}</b>\n"
            idx += 1
        message += "\n"
    return message

def send_daily_report():
    targets = load_targets()
    # fix #21: 定时日报使用持久化语言，手动消息仍由 Telegram 语言决定
    lang = get_language()
    if not targets:
        return send_msg(get_text("daily_report_title", lang) + "\n\n" + get_text("no_targets", lang), generate_inline_buttons(lang))
    body = format_numbered_targets(targets, lang).replace(f"                  <b>{get_text('current_targets_title', lang)}</b>\n\n", "")
    return send_msg(f"📅 <b>{get_text('daily_report_title', lang)}</b>\n\n{body}", generate_inline_buttons(lang))


# feat: 为目标生成续费周期选择按钮并过滤超长回调数据
def _renew_period_keyboard(name, lang):
    buttons = []
    option_keys = ((1, "renew_opt_1m"), (3, "renew_opt_3m"), (12, "renew_opt_12m"))
    for months, text_key in option_keys:
        callback_data = f"renew_opt:{months}"
        if len(callback_data.encode()) <= 58:
            buttons.append({"text": get_text(text_key, lang), "callback_data": callback_data})
    if not buttons:
        return None
    buttons.append({"text": get_text("back_button", lang), "callback_data": "renew_exit"})
    buttons.append({"text": get_text("cancel_button", lang), "callback_data": "renew_cancel"})
    return {"inline_keyboard": [buttons[:-2], buttons[-2:]]}


# feat: 为续费生成计算方式选择按钮并过滤超长回调数据
def _renew_mode_keyboard(name, months, lang):
    target = load_targets().get(name)
    if target is None:
        return None
    original_date = target.strftime("%Y-%m-%d")
    today_date = datetime.now(TIMEZONE).strftime("%Y-%m-%d")
    mode_buttons = []
    callbacks = (
        ("renew_mode_today", today_date, "today"),
        ("renew_mode_original", original_date, "orig"),
    )
    for text_key, date_str, mode in callbacks:
        callback_data = f"renew_mode:{months}:{mode}"
        if len(callback_data.encode()) <= 58:
            mode_buttons.append({
                "text": get_text(text_key, lang, date=date_str),
                "callback_data": callback_data,
            })
    footer = []
    back_data = "renew_back"
    if len(back_data.encode()) <= 58:
        footer.append({"text": get_text("back_button", lang), "callback_data": back_data})
    cancel_data = "renew_cancel"
    if len(cancel_data.encode()) <= 58:
        footer.append({"text": get_text("cancel_button", lang), "callback_data": cancel_data})
    keyboard = []
    if mode_buttons:
        keyboard.append(mode_buttons)
    if footer:
        keyboard.append(footer)
    return {"inline_keyboard": keyboard} if keyboard else None


# feat: 在面板中显示续费周期选择
def _send_renew_period_prompt(name, lang):
    user_state["renew_name"] = name
    user_state["renew_months"] = None
    keyboard = _renew_period_keyboard(name, lang)
    prompt = get_text("renew_period_prompt", lang, name=html.escape(name))
    if keyboard is None:
        prompt += "\n\n" + get_text("renew_usage", lang)
    return panel_send_or_edit(prompt, keyboard, new_state="renew_period")


# feat: 在面板中显示续费计算方式选择
def _send_renew_mode_prompt(name, months, lang):
    user_state["renew_months"] = months
    keyboard = _renew_mode_keyboard(name, months, lang)
    prompt = get_text("renew_mode_prompt", lang, name=html.escape(name), months=months)
    return panel_send_or_edit(prompt, keyboard, new_state="renew_mode")


def _edit_confirm_keyboard(lang):
    return {
        "inline_keyboard": [
            [{"text": get_text("renew_button", lang), "callback_data": "edit_confirm_renew"}],
            [
                {"text": get_text("back_button", lang), "callback_data": "edit_back"},
                {"text": get_text("cancel_button", lang), "callback_data": "edit_cancel"},
            ],
        ]
    }


def setup_bot_commands():
    """设置 Telegram 命令菜单，失败时仅记录警告。"""
    try:
        response = requests.post(
            f"{BASE_URL}setMyCommands",
            data={"commands": json.dumps([{
                "command": "start",
                "description": "打开主菜单 / Open main menu",
            }], ensure_ascii=False)},
            timeout=10,
        )
        if response.status_code != 200 or response.json().get("ok") is False:
            print("⚠️ Failed to set bot commands")
    except Exception as error:
        print(f"⚠️ Bot command setup warning: {type(error).__name__}")


# feat: 检查并发送每个目标命中的到期提醒节点
def check_and_send_node_reminders() -> bool:
    try:
        targets = load_targets()
        today = datetime.now(TIMEZONE).date()
        lang = get_language()
        for name, target_date in targets.items():
            days = (target_date.date() - today).days
            node = reminder_node_for(days)
            if node is None or has_reminded(name, node):
                continue
            escaped_name = html.escape(name)
            date_str = target_date.strftime("%Y-%m-%d")
            if days >= 0:
                message = get_text("reminder_due", lang, name=escaped_name, days=days, date=date_str)
            else:
                message = get_text("reminder_overdue", lang, name=escaped_name, days=-days, date=date_str)
            encoded_name = quote(name, safe="")
            callback_data = f"renew:{encoded_name}"
            keyboard = None
            if len(callback_data.encode()) <= 58:
                keyboard = {
                    "inline_keyboard": [[
                        {"text": get_text("renew_button", lang), "callback_data": callback_data}
                    ]]
                }
            else:
                message += "\n\n" + get_text("renew_usage", lang)
            if send_msg(message, keyboard):
                mark_reminded(name, node)
        return True
    except Exception as error:
        print(f"reminder warning: {type(error).__name__}")
        return False


def is_valid_date(date_str: str) -> bool:
    """检查日期字符串是否为有效 YYYY-MM-DD 格式"""
    if not date_str or not isinstance(date_str, str):
        return False
    return normalize_date(date_str) is not None


def is_valid_push_time(text: str) -> bool:
    """检查推送时间是否为有效 HH:MM 格式 (0-23:0-59)"""
    if not text or not isinstance(text, str):
        return False
    text = text.strip()
    # fix: Telegram 输入和数据库写入都只接受严格的两位 HH:MM
    if not re.fullmatch(r"\d{2}:\d{2}", text):
        return False
    try:
        h, m = map(int, text.split(":"))
        return 0 <= h <= 23 and 0 <= m <= 59
    except (ValueError, TypeError):
        return False


def handle_callback_query(update):
    global user_state
    if not is_authorized_update(update):
        # fix: 未授权回调静默丢弃，不进入共享业务状态
        callback_id = update.get("callback_query", {}).get("id")
        if callback_id:
            try:
                requests.post(
                    f"{BASE_URL}answerCallbackQuery",
                    data={"callback_query_id": callback_id, "show_alert": False},
                    timeout=10,
                )
            except requests.RequestException:
                pass
        return
    lang = get_user_lang(update)
    callback_data = update["callback_query"]["data"]
    requests.post(f"{BASE_URL}answerCallbackQuery", data={"callback_query_id": update["callback_query"]["id"]})
    callback_message = update["callback_query"].get("message")
    message_id = callback_message.get("message_id") if callback_message else None
    if message_id is not None:
        # feat: 用户点击的消息成为后续交互的面板锚点
        user_state["panel_message_id"] = message_id
    
    if callback_data == "action_edit":
        user_state["pending_action"] = "edit"
        panel_send_or_edit(
            get_text("edit_prompt", lang) + "\n\n" + format_numbered_targets(load_targets(), lang),
            generate_inline_buttons(lang),
            new_state="input_edit",
        )
    elif callback_data == "action_archive":
        user_state["pending_action"] = "archive"
        panel_send_or_edit(
            get_text("archive_prompt", lang) + "\n\n" + format_numbered_targets(load_targets(), lang),
            generate_inline_buttons(lang),
            new_state="input_archive",
        )
    elif callback_data == "action_renew":
        user_state["pending_action"] = "renew"
        panel_send_or_edit(
            get_text("renew_select_prompt", lang) + "\n\n" + format_numbered_targets(load_targets(), lang),
            generate_inline_buttons(lang),
            new_state="input_renew",
        )
    elif callback_data == "show_subscriptions":
        show_targets(update)
    elif callback_data == "add_target":
        panel_send_or_edit(get_text("add_target_prompt", lang), generate_inline_buttons(lang), new_state="input_add")
    elif callback_data == "set_time":
        panel_send_or_edit(get_text("set_time_prompt", lang), generate_inline_buttons(lang), new_state="input_time")
    elif callback_data == "export_data":
        data = export_all()
        json_str = json.dumps(data, ensure_ascii=False, indent=2)
        send_export(json_str, lang)
    elif callback_data == "import_data":
        user_state["pending_import"] = True
        panel_send_or_edit(get_text("import_prompt", lang), generate_inline_buttons(lang), new_state="input_import")
    # feat: 处理提醒消息进入续费周期选择
    elif callback_data.startswith("renew:"):
        name = unquote(callback_data[len("renew:"):])
        if name not in load_targets():
            panel_send_or_edit(get_text("renew_failed", lang), generate_inline_buttons(lang), new_state="main")
        else:
            _send_renew_period_prompt(name, lang)
    # feat: 处理续费周期回调并进入计算方式选择
    elif callback_data.startswith("renew_opt:"):
        months_str = callback_data[len("renew_opt:"):]
        name = user_state.get("renew_name")
        try:
            months = int(months_str)
        except ValueError:
            months = None
        if months in {1, 3, 12} and name:
            _send_renew_mode_prompt(name, months, lang)
        else:
            panel_send_or_edit(get_text("renew_failed", lang), generate_inline_buttons(lang), new_state="main")
    elif callback_data.startswith("renew_mode:"):
        months_str, separator, mode = callback_data[len("renew_mode:"):].partition(":")
        name = user_state.get("renew_name")
        try:
            months = int(months_str) if separator else None
        except ValueError:
            months = None
        base = {"today": "today", "orig": "original"}.get(mode)
        new_date = renew_target(name, months, base) if name and months in {1, 3, 12} and base else None
        if new_date:
            success_text = get_text("renew_success", lang, name=html.escape(name), date=new_date)
            panel_send_or_edit(success_text, generate_inline_buttons(lang), new_state="main")
            show_targets(update)
        else:
            panel_send_or_edit(get_text("renew_failed", lang), generate_inline_buttons(lang), new_state="main")
    elif callback_data == "renew_back":
        name = user_state.get("renew_name")
        if name:
            _send_renew_period_prompt(name, lang)
        else:
            panel_send_or_edit(get_text("renew_failed", lang), generate_inline_buttons(lang), new_state="main")
    elif callback_data == "edit_confirm_renew":
        name = user_state.get("pending_edit_target")
        if name and name in load_targets():
            user_state["pending_edit_target"] = None
            _send_renew_period_prompt(name, lang)
        else:
            panel_send_or_edit(get_text("renew_failed", lang), generate_inline_buttons(lang), new_state="main")
    elif callback_data == "edit_back":
        user_state["pending_edit_target"] = None
        show_targets(update)
    elif callback_data == "edit_cancel":
        user_state["pending_edit_target"] = None
        panel_send_or_edit(get_text("cancelled", lang), generate_inline_buttons(lang), new_state="main")
    elif callback_data == "renew_exit" or callback_data == "menu_exit":
        show_targets(update)
    elif callback_data == "renew_cancel" or callback_data == "menu_cancel":
        panel_send_or_edit(get_text("cancelled", lang), generate_inline_buttons(lang), new_state="main")
    elif callback_data == "close_menu":
        panel_message_id = user_state.get("panel_message_id")
        if panel_message_id is not None:
            delete_message(TG_USER_ID, panel_message_id)
        user_state["panel_message_id"] = None
        user_state["panel_state"] = None

def handle_message(update):
    global user_state
    if not is_authorized_update(update):
        # fix: 未授权消息静默忽略，避免外部用户修改单用户状态
        return
    if not update.get("message", {}).get("text"):
        # fix: 明确忽略图片、贴纸、文件等非文本消息
        return
    lang = get_user_lang(update)
    text = update["message"]["text"].strip()
    command, args = _parse_command(text)

    if command == "/start":
        show_targets(update)
        return

    # feat: 支持通过 /renew 和完整名称发起续费顺延
    if command == "/renew":
        name = args.strip()
        if not name:
            panel_send_or_edit(get_text("renew_usage", lang), generate_inline_buttons(lang), new_state="main")
        elif name not in load_targets():
            panel_send_or_edit(get_text("renew_failed", lang), generate_inline_buttons(lang), new_state="main")
        else:
            _send_renew_period_prompt(name, lang)
        return

    if command == "/language":
        requested_lang = args.strip().lower()
        if requested_lang not in {"zh", "en"} or not set_language(requested_lang):
            panel_send_or_edit(get_text("language_usage", lang), generate_inline_buttons(lang), new_state="main")
        else:
            panel_send_or_edit(get_text("language_set", requested_lang), generate_inline_buttons(requested_lang), new_state="main")
        return

    if user_state["pending_action"] and text.isdigit():
        idx = int(text)
        targets = load_targets()
        sorted_targets = sorted(targets.items(), key=lambda x: x[1])
        
        if idx == 0 and user_state["pending_action"] == "archive":
            archives = load_archives()
            if not archives:
                panel_send_or_edit(get_text("no_archived", lang), generate_inline_buttons(lang), new_state="main")
            else:
                msg = get_text("archived_history", lang)
                for name, target_date in sorted(archives.items(), key=lambda x: x[1], reverse=True):
                    # fix: 归档名称是用户输入，插入 HTML 前必须转义
                    msg += f"• {html.escape(name)}: {target_date.strftime('%Y-%m-%d')}\n"
                panel_send_or_edit(msg, generate_inline_buttons(lang), new_state="main")
            user_state["pending_action"] = None
            return

        if 1 <= idx <= len(sorted_targets):
            old_name = sorted_targets[idx-1][0]
            current_date = targets[old_name].strftime("%Y-%m-%d")
            
            if user_state["pending_action"] == "edit":
                user_state["pending_edit_target"] = old_name
                user_state["pending_action"] = None
                panel_send_or_edit(
                    get_text("edit_current", lang, name=html.escape(old_name), date=current_date),
                    _edit_confirm_keyboard(lang),
                    new_state="edit_confirm",
                )
                return
            elif user_state["pending_action"] == "archive":
                if archive_target(old_name):
                    panel_send_or_edit(get_text("archive_success", lang, name=html.escape(old_name)), generate_inline_buttons(lang), new_state="main")
                    show_targets(update)
                else:
                    panel_send_or_edit(get_text("archive_failed", lang), generate_inline_buttons(lang), new_state="main")
                user_state["pending_action"] = None
                return
            elif user_state["pending_action"] == "renew":
                _send_renew_period_prompt(old_name, lang)
                user_state["pending_action"] = None
                return

    if user_state["pending_edit_target"] and text:
        old_name = user_state["pending_edit_target"]
        new_name = None
        new_date = None
        parsed = _parse_name_date(text)
        if parsed:
            new_name, new_date = parsed
        elif is_valid_date(text):
            new_date = normalize_date(text)
        elif len(text.split()) == 1 and not re.search(r"\d", text):
            # fix #16: 保留既有单 token 只改名称语义，多词输入必须带日期
            new_name = text.strip()
        else:
            # fix #16: 编辑解析失败时保留状态，不静默修改目标
            panel_send_or_edit(get_text("edit_format_error", lang), generate_inline_buttons(lang), new_state="input_edit")
            return
        updated = update_target(old_name, new_name, new_date)
        if updated:
            panel_send_or_edit(get_text("edit_success", lang), generate_inline_buttons(lang), new_state="main")
        else:
            panel_send_or_edit(get_text("edit_failed", lang), generate_inline_buttons(lang), new_state="main")
        user_state["pending_edit_target"] = None
        if updated:
            # fix #20: 编辑操作完成后由编辑流程主动清理状态
            show_targets(update)
        return

    if user_state["pending_import"]:
        try:
            if len(text.encode("utf-8")) > 1_000_000:
                raise ValueError("import too large")
            import_data = json.loads(text)
            if not _json_depth_ok(import_data) or not validate_import_data(import_data):
                raise ValueError("invalid import structure")
            result = import_all(import_data)
            panel_send_or_edit(
                get_text(
                    "import_summary",
                    lang,
                    targets=result["targets_imported"],
                    archives=result["archives_imported"],
                    conflicts=result["conflicts"],
                    skipped=result["skipped"],
                ),
                generate_inline_buttons(lang),
                new_state="main",
            )
            show_targets(update)
        except Exception as e:
            # fix #13: 限制导入大小和结构，失败时只返回固定描述
            panel_send_or_edit(get_text("import_invalid", lang), generate_inline_buttons(lang), new_state="input_import")
        user_state["pending_import"] = False
        return

    if command == "/addsub":
        parsed = _parse_name_date(args)
        if parsed:
            name, date_str = parsed
            from .db import add_target
            if add_target(name, date_str):
                panel_send_or_edit(get_text("add_success", lang), generate_inline_buttons(lang), new_state="main")
                show_targets(update)
            else:
                panel_send_or_edit(get_text("add_failed", lang), generate_inline_buttons(lang), new_state="main")
        else:
            panel_send_or_edit(get_text("format_error", lang), generate_inline_buttons(lang), new_state="input_add")
        return

    elif command == "/export":
        data = export_all()
        json_str = json.dumps(data, ensure_ascii=False, indent=2)
        send_export(json_str, lang)

    elif command == "/import":
        user_state["pending_import"] = True
        panel_send_or_edit(get_text("import_prompt", lang), generate_inline_buttons(lang), new_state="input_import")

    elif is_valid_push_time(text):
        from .db import set_push_time
        if set_push_time(text):
            panel_send_or_edit(get_text("push_time_set", lang, time=get_push_time()), generate_inline_buttons(lang), new_state="main")
        else:
            panel_send_or_edit(get_text("push_time_failed", lang), generate_inline_buttons(lang), new_state="input_time")
        return

    elif command == "/subs" or (command == "/list" and args.strip().lower() == "all"):
        show_targets(update)

def _targets_message(lang):
    targets = load_targets()
    formatted = format_numbered_targets(targets, lang)
    keyboard = generate_inline_buttons(lang)
    return formatted, keyboard


def show_targets(update):
    lang = get_user_lang(update)
    # fix #20: 展示列表不清理尚未完成的交互状态
    formatted, keyboard = _targets_message(lang)
    return panel_send_or_edit(formatted, keyboard, new_state="main")


def _json_depth_ok(value, depth=0):
    if depth > 5:
        return False
    if isinstance(value, dict):
        return all(_json_depth_ok(key, depth + 1) and _json_depth_ok(item, depth + 1) for key, item in value.items())
    if isinstance(value, list):
        return all(_json_depth_ok(item, depth + 1) for item in value)
    return True

def poll_updates():
    global last_offset
    url = f"{BASE_URL}getUpdates"
    # fix: 宿主网络会概率断开 ~30s+ 的空闲长连接，缩短轮询窗口规避
    params = {"timeout": 20, "offset": last_offset, "allowed_updates": ["message", "callback_query"]}
    try:
        response = requests.get(url, params=params, timeout=25)
        if response.status_code != 200:
            # fix #4: 区分鉴权、冲突和限流错误，交由唯一入口选择退避策略
            if response.status_code == 401:
                print("❌ Failed to fetch updates: HTTP 401")
                return "auth"
            if response.status_code == 409:
                print("❌ Failed to fetch updates: HTTP 409")
                return "conflict"
            if response.status_code == 429:
                print("❌ Failed to fetch updates: HTTP 429")
                return "rate_limit"
            print(f"❌ Failed to fetch updates: HTTP {response.status_code}")
            return False
        data = response.json()
        if not data.get("ok", True):
            print("❌ Failed to fetch updates: Telegram API error")
            return False
        next_offset = last_offset
        failed_update = False
        for update in data.get("result", []):
            try:
                if is_authorized_update(update):
                    _queue_user_message(update)
                if "callback_query" in update:
                    handle_callback_query(update)
                elif "message" in update:
                    handle_message(update)
                else:
                    # fix #3: 无关 update 显式忽略并确认，避免阻塞后续消息
                    if not failed_update:
                        next_offset = update["update_id"] + 1
                    continue
                if not failed_update:
                    next_offset = update["update_id"] + 1
            except Exception as e:
                print(f"❌ Update handling failed: {type(e).__name__}")
                failed_update = True
        # fix #3: 只提交失败前的连续 offset，失败消息会在下轮重试
        last_offset = next_offset
        return True
    except requests.RequestException as e:
        # fix #4: 网络异常与业务异常分开，主循环可执行退避重连
        print(f"❌ Failed to fetch updates: {type(e).__name__}")
        return False
    except (ValueError, json.JSONDecodeError) as e:
        print(f"❌ Failed to parse updates: {type(e).__name__}")
        return False
