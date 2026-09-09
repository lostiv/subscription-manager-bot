from datetime import datetime
from .config import TIMEZONE


# feat: 定义到期前和当天的提醒档位
REMINDER_NODES = (30, 7, 3, 1, 0)


# feat: 根据剩余自然日计算提醒节点
def reminder_node_for(days: int):
    """命中 30/7/3/1/0 返回节点字符串，逾期返回 overdue。"""
    if days in REMINDER_NODES:
        return str(days)
    if days < 0:
        return "overdue"
    return None


def format_msg(targets: dict) -> str:
    """定时推送使用的简洁消息"""
    if not targets:
        return "📅 当前没有任何目标"

    message = ""
    sorted_targets = sorted(targets.items(), key=lambda x: x[1])

    for name, target_date in sorted_targets:
        # fix #15: 倒计时按本地自然日计算，避免当天午夜后的负数误判
        days_left = target_date.date() - datetime.now(TIMEZONE).date()
        days_left = days_left.days
        if days_left < 0:
            message += f"{name}: 已结束\n"
        elif days_left == 0:
            message += f"{name}: <b>今天</b>\n"
        elif days_left <= 3:
            message += f"{name}: <b>{days_left}天 (紧急)</b>\n"
        else:
            message += f"{name}: {days_left}天\n"
    return message


def get_formatted_targets(targets: dict) -> str:
    """手动查看时使用的美观格式"""
    if not targets:
        return "📅 当前没有任何目标"

    message = "📅 <b>当前倒计时目标列表</b>:\n\n"
    sorted_targets = sorted(targets.items(), key=lambda x: x[1])

    for name, target_date in sorted_targets:
        # fix #15: 手动查看与日报统一使用本地自然日口径
        days_left = target_date.date() - datetime.now(TIMEZONE).date()
        days_left = days_left.days
        if days_left < 0:
            message += f"<i>{name}: 已结束</i>\n"
        elif days_left == 0:
            message += f"<b>{name}: 今天</b>\n"
        elif days_left <= 9:
            message += f"<b>{name}: {days_left}天 (紧急)</b>\n"
        else:
            message += f"{name}: {days_left}天\n"
    return message
