import time
import re
from datetime import datetime
from .db import load_targets, get_push_time
from .telegram import send_daily_report   # 定时推送使用
from .config import TIMEZONE

# 使用 (日期, 小时, 分钟) 作为 key，支持当天多次修改未来时间后继续推送
last_pushed_key = None
invalid_time_logged = False

def push_loop():
    global last_pushed_key, invalid_time_logged
    while True:
        try:
            # fix #6: 调度使用 Asia/Shanghai 的带时区当前时间
            now = datetime.now(TIMEZONE)
            t = get_push_time()
            try:
                # fix #14: 调度器同样严格要求两位 HH:MM，非法值只告警一次并跳过
                if not isinstance(t, str) or not re.fullmatch(r"\d{2}:\d{2}", t):
                    raise ValueError("invalid HH:MM")
                parsed_time = datetime.strptime(t, "%H:%M")
                hour, minute = parsed_time.hour, parsed_time.minute
                invalid_time_logged = False
            except (TypeError, ValueError):
                if not invalid_time_logged:
                    print("scheduler warning: invalid push time, skipping this cycle")
                    invalid_time_logged = True
                time.sleep(15)
                continue
            today = now.date()

            current_key = (today, hour, minute)
            scheduled_at = now.replace(hour=hour, minute=minute, second=0, microsecond=0)

            # fix #5: 到达目标时刻后当天只成功推送一次，不受错过分钟窗口影响
            if now >= scheduled_at and current_key != last_pushed_key:
                # fix #7: 仅发送成功后记录，发送失败时下轮继续重试
                if send_daily_report():
                    last_pushed_key = current_key
                    print(f"✅ 定时日报已发送 - {t} ({now.strftime('%H:%M:%S')})")
                else:
                    print("scheduler warning: daily report send failed, will retry")
        except Exception as e:
            print(f"scheduler error: {e}")

        time.sleep(15)
