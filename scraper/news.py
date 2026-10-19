"""
연예·방송 이슈 수집 + 텔레그램 알림
- 구글 뉴스 RSS(키 불필요)와, 키가 있으면 네이버 뉴스 검색 API에서 기사를 가져옵니다.
- 이미 본 기사는 data/news_seen.json 에 기록해 중복 알림을 막습니다.
- 결과는 data/news.json (페이지 표시용)에 저장됩니다.

GitHub 저장소 Settings → Secrets and variables → Actions 에 등록:
  TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID      (알림, 필수)
  NAVER_CLIENT_ID, NAVER_CLIENT_SECRET      (네이버 뉴스, 선택)
"""
import datetime as dt
import email.utils
import html
import json
import os
import pathlib
import re
import urllib.parse
import xml.etree.ElementTree as ET

import requests

KST = dt.timezone(dt.timedelta(hours=9))
ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "news.json"
SEEN = ROOT / "data" / "news_seen.json"

# 수집할 키워드 (원하는 대로 수정)
KEYWORDS = ["연예", "드라마 시청률", "예능", "아이돌 컴백", "열애", "결혼 발표"]
KEEP_HOURS = 24          # 페이지에 보여줄 기간
MAX_ALERT = 15           # 한 번에 보낼 최대 알림 수
HEADERS = {"User-Agent": "Mozilla/5.0 tvguide-news"}


def clean(s):
    return html.unescape(re.sub(r"<[^>]+>", "", s or "")).strip()


def google_news(q):
    url = ("https://news.google.com/rss/search?hl=ko&gl=KR&ceid=KR:ko&q="
           + urllib.parse.quote(q + " when:1d"))
    r = requests.get(url, headers=HEADERS, timeout=20)
    r.raise_for_status()
    out = []
    for it in ET.fromstring(r.content).iter("item"):
        title = clean(it.findtext("title"))
        src = clean(it.findtext("source"))
        if src and title.endswith(" - " + src):
            title = title[: -len(src) - 3]
        out.append({"title": title, "link": it.findtext("link"), "source": src,
                    "time": email.utils.parsedate_to_datetime(it.findtext("pubDate")).isoformat(),
                    "keyword": q})
    return out


def naver_news(q):
    cid, sec = os.getenv("NAVER_CLIENT_ID"), os.getenv("NAVER_CLIENT_SECRET")
    if not (cid and sec):
        return []
    r = requests.get("https://openapi.naver.com/v1/search/news.json",
                     params={"query": q, "display": 30, "sort": "date"},
                     headers={"X-Naver-Client-Id": cid, "X-Naver-Client-Secret": sec}, timeout=20)
    r.raise_for_status()
    return [{"title": clean(i["title"]), "link": i.get("originallink") or i["link"],
             "source": urllib.parse.urlparse(i.get("originallink") or i["link"]).netloc,
             "time": email.utils.parsedate_to_datetime(i["pubDate"]).isoformat(),
             "keyword": q} for i in r.json().get("items", [])]


def norm(t):
    """제목 유사 중복 판별용 키."""
    return re.sub(r"[\W_]+", "", re.sub(r"\[[^\]]*\]", "", t))[:40]


def telegram(items):
    token, chat = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    if not (token and chat) or not items:
        return
    lines = [f"📺 새 연예 이슈 {len(items)}건"]
    for a in items:
        lines.append(f"\n• <a href=\"{html.escape(a['link'])}\">{html.escape(a['title'])}</a>"
                     f" <i>({html.escape(a['source'] or a['keyword'])})</i>")
    requests.post(f"https://api.telegram.org/bot{token}/sendMessage", timeout=20,
                  json={"chat_id": chat, "text": "".join(lines), "parse_mode": "HTML",
                        "disable_web_page_preview": True}).raise_for_status()


def main():
    now = dt.datetime.now(KST)
    seen = set(json.loads(SEEN.read_text())) if SEEN.exists() else set()
    first_run = not SEEN.exists()

    articles = []
    for q in KEYWORDS:
        for fn in (google_news, naver_news):
            try:
                articles += fn(q)
            except Exception as e:
                print(f"[실패] {fn.__name__}({q}): {e}")

    cutoff = now - dt.timedelta(hours=KEEP_HOURS)
    uniq = {}
    for a in articles:
        if dt.datetime.fromisoformat(a["time"]) >= cutoff:
            uniq.setdefault(norm(a["title"]), a)
    fresh = sorted(uniq.values(), key=lambda a: a["time"], reverse=True)

    new = [a for a in fresh if norm(a["title"]) not in seen]
    print(f"수집 {len(fresh)}건, 새 기사 {len(new)}건")
    if not first_run:   # 첫 실행은 기존 기사로 알림 폭탄이 가지 않도록 건너뜀
        try:
            telegram(new[:MAX_ALERT])
        except Exception as e:
            print(f"::warning::텔레그램 전송 실패: {e}")

    seen |= {norm(a["title"]) for a in fresh}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    SEEN.write_text(json.dumps(sorted(seen)[-3000:], ensure_ascii=False))
    OUT.write_text(json.dumps({"generated": now.isoformat(), "articles": fresh[:100]},
                              ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
