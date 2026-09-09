import time
import threading

from app.db import init_db
from app.scheduler import push_loop
from app.telegram import poll_updates
from app.config import DB_PATH, TIMEZONE

stop_event = threading.Event()

def initialize():
    init_db()
    print("✅ 数据库已初始化")
    # fix #22: 启动时输出不含密钥的运行配置摘要
    from app.db import get_push_time
    print(f"配置摘要: 时区={TIMEZONE.key}, 推送时间={get_push_time()}, DB路径={DB_PATH}")

def start_scheduler():
    thread = threading.Thread(target=push_loop, args=(stop_event,))
    thread.daemon = True
    thread.start()
    print("✅ 定时推送任务已启动")
    return thread

def start_telegram_polling():
    thread = threading.Thread(target=poll_loop, args=(stop_event,))
    thread.daemon = True
    thread.start()
    print("✅ Telegram 消息轮询已启动")
    return thread

def poll_loop(stop_event=None):
    retry_delay = 1
    while stop_event is None or not stop_event.is_set():
        result = poll_updates()
        if result is True:
            retry_delay = 1
            if stop_event is None:
                time.sleep(0.2)
            else:
                stop_event.wait(0.2)
        elif result == "auth":
            if stop_event is None:
                time.sleep(60)
            else:
                stop_event.wait(60)
        elif result == "conflict":
            if stop_event is None:
                time.sleep(10)
            else:
                stop_event.wait(10)
        elif result == "rate_limit":
            delay = min(retry_delay * 2, 60)
            if stop_event is None:
                time.sleep(delay)
            else:
                stop_event.wait(delay)
            retry_delay = min(retry_delay * 2, 60)
        else:
            # fix: 长轮询断连是宿主网络常态，固定等待 2 秒后快速重连
            if stop_event is None:
                time.sleep(2)
            else:
                stop_event.wait(2)

if __name__ == "__main__":
    initialize()
    scheduler_thread = start_scheduler()
    telegram_thread = start_telegram_polling()

    try:
        while not stop_event.is_set():
            time.sleep(1)
    except KeyboardInterrupt:
        # fix #22: 主线程中断时通知两个 daemon 工作线程停止
        stop_event.set()
        scheduler_thread.join(timeout=5)
        telegram_thread.join(timeout=5)
        print("🛑 机器人已停止")
