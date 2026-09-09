import requests
import time
import json
import html
import re
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
)
from .config import BOT_TOKEN, TG_USER_ID, BASE_URL, TIMEZONE

last_offset = 0

user_state = {
    "pending_action": None,
    "pending_edit_target": None,
    "pending_import": False
}

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

    "edit_prompt": {"en": "✏️ Please enter the <b>number</b> of the target to edit (e.g. 1 or 2...)", "zh": "✏️ 请输入要<b>修改</b>的目标序号（例如：1或2...）"},
    "archive_prompt": {"en": "📦 Please enter the <b>number</b> of the target to archive (enter <b>0</b> to view all archived; enter <b>1 or 2...</b> to archive)", "zh": "📦 请输入要<b>归档</b>的目标序号（输入 <b>0</b> 查看所有历史归档;输入<b>1或2...</b> 归档目标）"},
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
            return False
        if response.json().get("ok") is False:
            print("❌ Send failed: Telegram API error")
            return False
        else:
            # fix #7: 调用方通过返回值判断发送是否成功
            print("✅ Message sent successfully")
            return True
    except Exception as e:
        print(f"❌ Send error: {type(e).__name__}")
        return False


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
    
    if callback_data == "action_edit":
        user_state["pending_action"] = "edit"
        send_msg(get_text("edit_prompt", lang), generate_inline_buttons(lang))
    elif callback_data == "action_archive":
        user_state["pending_action"] = "archive"
        send_msg(get_text("archive_prompt", lang), generate_inline_buttons(lang))
    elif callback_data == "show_subscriptions":
        show_targets(update)
    elif callback_data == "add_target":
        send_msg(get_text("add_target_prompt", lang), generate_inline_buttons(lang))
    elif callback_data == "set_time":
        send_msg(get_text("set_time_prompt", lang), generate_inline_buttons(lang))
    elif callback_data == "export_data":
        data = export_all()
        json_str = json.dumps(data, ensure_ascii=False, indent=2)
        send_export(json_str, lang)
    elif callback_data == "import_data":
        user_state["pending_import"] = True
        send_msg(get_text("import_prompt", lang), generate_inline_buttons(lang))

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
        send_msg(get_text("start_welcome", lang), generate_inline_buttons(lang))
        return

    if command == "/language":
        requested_lang = args.strip().lower()
        if requested_lang not in {"zh", "en"} or not set_language(requested_lang):
            send_msg(get_text("language_usage", lang), generate_inline_buttons(lang))
        else:
            send_msg(get_text("language_set", requested_lang), generate_inline_buttons(requested_lang))
        return

    if user_state["pending_action"] and text.isdigit():
        idx = int(text)
        targets = load_targets()
        sorted_targets = sorted(targets.items(), key=lambda x: x[1])
        
        if idx == 0 and user_state["pending_action"] == "archive":
            archives = load_archives()
            if not archives:
                send_msg(get_text("no_archived", lang), generate_inline_buttons(lang))
            else:
                msg = get_text("archived_history", lang)
                for name, target_date in sorted(archives.items(), key=lambda x: x[1], reverse=True):
                    # fix: 归档名称是用户输入，插入 HTML 前必须转义
                    msg += f"• {html.escape(name)}: {target_date.strftime('%Y-%m-%d')}\n"
                send_msg(msg, generate_inline_buttons(lang))
            user_state["pending_action"] = None
            return

        if 1 <= idx <= len(sorted_targets):
            old_name = sorted_targets[idx-1][0]
            current_date = targets[old_name].strftime("%Y-%m-%d")
            
            if user_state["pending_action"] == "edit":
                user_state["pending_edit_target"] = old_name
                user_state["pending_action"] = None
                send_msg(get_text("edit_current", lang, name=html.escape(old_name), date=current_date), generate_inline_buttons(lang))
                return
            elif user_state["pending_action"] == "archive":
                if archive_target(old_name):
                    send_msg(get_text("archive_success", lang, name=html.escape(old_name)), generate_inline_buttons(lang))
                    show_targets(update)
                else:
                    send_msg(get_text("archive_failed", lang), generate_inline_buttons(lang))
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
            send_msg(get_text("edit_format_error", lang), generate_inline_buttons(lang))
            return
        updated = update_target(old_name, new_name, new_date)
        if updated:
            send_msg(get_text("edit_success", lang), generate_inline_buttons(lang))
        else:
            send_msg(get_text("edit_failed", lang), generate_inline_buttons(lang))
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
            send_msg(
                get_text(
                    "import_summary",
                    lang,
                    targets=result["targets_imported"],
                    archives=result["archives_imported"],
                    conflicts=result["conflicts"],
                    skipped=result["skipped"],
                ),
                generate_inline_buttons(lang),
            )
            show_targets(update)
        except Exception as e:
            # fix #13: 限制导入大小和结构，失败时只返回固定描述
            send_msg(get_text("import_invalid", lang), generate_inline_buttons(lang))
        user_state["pending_import"] = False
        return

    if command == "/addsub":
        parsed = _parse_name_date(args)
        if parsed:
            name, date_str = parsed
            from .db import add_target
            if add_target(name, date_str):
                send_msg(get_text("add_success", lang), generate_inline_buttons(lang))
                show_targets(update)
            else:
                send_msg(get_text("add_failed", lang), generate_inline_buttons(lang))
        else:
            send_msg(get_text("format_error", lang), generate_inline_buttons(lang))
        return

    elif command == "/export":
        data = export_all()
        json_str = json.dumps(data, ensure_ascii=False, indent=2)
        send_export(json_str, lang)

    elif command == "/import":
        user_state["pending_import"] = True
        send_msg(get_text("import_prompt", lang), generate_inline_buttons(lang))

    elif is_valid_push_time(text):
        from .db import set_push_time
        if set_push_time(text):
            send_msg(get_text("push_time_set", lang, time=get_push_time()), generate_inline_buttons(lang))
        else:
            send_msg(get_text("push_time_failed", lang), generate_inline_buttons(lang))
        return

    elif command == "/subs" or (command == "/list" and args.strip().lower() == "all"):
        show_targets(update)

def show_targets(update):
    lang = get_user_lang(update)
    # fix #20: 展示列表不清理尚未完成的交互状态
    targets = load_targets()
    formatted = format_numbered_targets(targets, lang)
    keyboard = generate_inline_buttons(lang)
    send_msg(formatted, keyboard)


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
    params = {"timeout": 100, "offset": last_offset, "allowed_updates": ["message", "callback_query"]}
    try:
        response = requests.get(url, params=params, timeout=110)
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
