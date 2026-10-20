"""
정해진 시간에 텔레그램으로 편성표 요약 발송.
매시간 실행되지만, 아래 SCHEDULE 에 적힌 시각(KST)에만 보냅니다.
"""
import datetime as dt
import html
import json
import os
import pathlib

import requests

KST = dt.timezone(dt.timedelta(hours=9))
ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "schedule.json"

# 발송 시각(KST 시) : (제목, 보여줄 시간대 시작시, 끝시)
SCHEDULE = {
    8:  ("☀️ 오늘의 편성표 (저녁 황금시간대)", 18, 24),
    19: ("🌙 오늘 밤 편성표", 19, 25),   # 25 = 다음날 01시
}


def build(title, h_from, h_to, now):
    data = json.loads(DATA.read_text(encoding="utf-8"))
    base = dt.datetime(now.year, now.month, now.day, tzinfo=KST)
    a, b = base + dt.timedelta(hours=h_from), base + dt.timedelta(hours=h_to)
    lines = [f"<b>{title}</b>", f"{now:%m월 %d일} {['월','화','수','목','금','토','일'][now.weekday()]}요일"]
    for ch in data["channels"]:
        progs = [p for p in ch["programs"]
                 if a <= dt.datetime.fromisoformat(p["start"]).astimezone(KST) < b]
        if not progs:
            continue
        lines.append(f"\n<b>📺 {html.escape(ch['name'])}</b>" + ("" if ch.get("ok", True) else " ⚠"))
        for p in progs:
            t = dt.datetime.fromisoformat(p["start"]).astimezone(KST)
            lines.append(f"{t:%H:%M} {html.escape(p['title'])}")
    site = os.getenv("SITE_URL")
    if site:
        lines.append(f"\n🔗 전체 편성표: {site}")
    return "\n".join(lines)


def main():
    now = dt.datetime.now(KST)
    force = os.getenv("DIGEST_FORCE")          # 수동 실행 시 테스트용
    hour = int(force) if force else now.hour
    if hour not in SCHEDULE:
        print(f"{now:%H}시: 발송 시각 아님")
        return
    token, chat = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    if not (token and chat and DATA.exists()):
        print("토큰/채팅ID/편성 데이터 없음 → 건너뜀")
        return
    # 같은 시각에 두 번 실행돼도(예약 실행 중복) 한 번만 발송
    mark = ROOT / "data" / "digest_sent.txt"
    key = f"{now:%Y-%m-%d} {hour:02d}"
    if not force and mark.exists() and mark.read_text().strip() == key:
        print("이미 발송함 → 건너뜀")
        return
    text = build(*SCHEDULE[hour], now)
    for i in range(0, len(text), 4000):        # 텔레그램 길이 제한
        requests.post(f"https://api.telegram.org/bot{token}/sendMessage", timeout=20,
                      json={"chat_id": chat, "text": text[i:i + 4000], "parse_mode": "HTML",
                            "disable_web_page_preview": True}).raise_for_status()
    if not force:
        mark.write_text(key)
    print("편성표 발송 완료")


if __name__ == "__main__":
    main()
