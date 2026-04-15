"""Ethiopian trading session validator based on ECX rules."""

from datetime import datetime, time

# East Africa Time offset (UTC+3)
EAT_OFFSET_HOURS = 3

DAY_MAP = {
    0: "Mon",
    1: "Tue",
    2: "Wed",
    3: "Thu",
    4: "Fri",
    5: "Sat",
    6: "Sun",
}


def is_trading_open(session_days: str | None, session_start: str | None, session_end: str | None) -> dict:
    """
    Check if trading is currently open for an asset based on ECX session rules.
    Non-ECX assets (sukuk, equities) have no session restriction.
    """
    # Non-ECX assets trade anytime (within platform hours)
    if not session_days or not session_start or not session_end:
        return {"is_open": True, "reason": "No session restriction"}

    now = datetime.utcnow()
    # Simple EAT approximation
    eat_hour = (now.hour + EAT_OFFSET_HOURS) % 24
    eat_minute = now.minute
    current_day = DAY_MAP[now.weekday()]

    allowed_days = [d.strip() for d in session_days.split(",")]
    if current_day not in allowed_days:
        return {
            "is_open": False,
            "reason": f"Trading only on {session_days}. Today is {current_day}.",
            "next_session": f"Next session: {allowed_days[0]}",
        }

    start_parts = session_start.split(":")
    end_parts = session_end.split(":")
    start = time(int(start_parts[0]), int(start_parts[1]))
    end = time(int(end_parts[0]), int(end_parts[1]))
    current = time(eat_hour, eat_minute)

    if start <= current <= end:
        return {"is_open": True, "reason": "Trading session active"}

    return {
        "is_open": False,
        "reason": f"Trading hours: {session_start} - {session_end} EAT",
        "current_time_eat": f"{eat_hour:02d}:{eat_minute:02d}",
    }
