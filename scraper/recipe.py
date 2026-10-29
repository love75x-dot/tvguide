"""
방송 속 요리·레시피 알림 (매시간)
  요리 프로그램·예능에서 레시피를 공개한 기사를 네이버 뉴스에서 찾아 텔레그램으로 보냄
  요리 프로그램이 방금 끝났으면 그 프로그램 이름으로 한 번 더 찾음
결과: data/recipes.json (최근 3일), data/recipes_seen.json
"""
import datetime as dt
import html
import json
import os
import re
import time

import trends as T
import hub as H
import marts as M

OUT = T.DATA / "recipes.json"
SEEN = T.DATA / "recipes_seen.json"
SHOWS = ["편스토랑", "냉장고를 부탁해", "알토란", "생생정보", "생활의 달인", "백종원", "수미네", "강식당",
         "나 혼자 산다", "전지적 참견 시점", "아는 형님", "놀면 뭐하니", "삼시세끼", "흑백요리사",
         "6시 내고향", "VJ특공대", "생방송 투데이", "생방송 오늘저녁", "2TV 생생정보", "맛있는 녀석들",
         "토요일은 밥이 좋아", "줄 서는 식당", "식객 허영만", "오늘N"]
QUERIES = ["레시피 공개", "방송 레시피", "황금레시피", "만드는 법 공개", "편스토랑 레시피", "냉장고를 부탁해 요리",
           "알토란 레시피", "백종원 레시피", "예능 요리 레시피", "유튜브 레시피 공개", "유튜브 요리 공개", "SNS 레시피 공개"]
COOK = re.compile(r"레시피|만드는\s?법|비법|요리|소스|메뉴|양념|반찬|김치|볶음|찌개|국수|라면|덮밥|디저트|베이킹")
STRONG = re.compile(r"레시피|만드는\s?법|비법\s?(?:공개|소스|양념)|꿀조합")
NOISE = re.compile(r"주가|증시|매출|실적|출시|프랜차이즈|가맹|편의점 신상|분양|부동산")


def show_of(title):
    t = re.sub(r"\s+", "", title)
    for s in SHOWS:
        if re.sub(r"\s+", "", s) in t:
            return s
    return None


def just_ended():
    """지난 2시간 안에 끝난 요리 프로그램 이름"""
    try:
        sc = json.loads((T.DATA / "schedule.json").read_text(encoding="utf-8"))
    except Exception:
        return []
    out = set()
    for ch in sc["channels"]:
        for p in ch["programs"]:
            try:
                e = dt.datetime.fromisoformat(p["end"])
            except Exception:
                continue
            if T.NOW - dt.timedelta(hours=2) <= e <= T.NOW:
                s = show_of(p["title"])
                if s:
                    out.add(s)
    return sorted(out)


def collect():
    if not os.getenv("NAVER_HUB_ID"):
        return
    H.probe()
    ended = just_ended()
    qs = QUERIES + [f"{s} 레시피" for s in ended] + [f"{s} 요리" for s in ended]
    cut = (T.NOW - dt.timedelta(days=3)).isoformat()
    got = {}
    for q in qs:
        try:
            _, path = H.ROUTES["news"]
            r = T.S.get(H.BASE + path, params={"query": q, "display": 30, "sort": "date"}, headers=H.headers(), timeout=20)
            r.raise_for_status()
            items = r.json().get("items", [])
        except Exception as e:
            print(f"  (레시피 검색 실패 {q}: {e})")
            continue
        for i in items:
            try:
                t = dt.datetime.strptime(i["pubDate"], "%a, %d %b %Y %H:%M:%S %z").isoformat()
            except Exception:
                continue
            title, desc = T.txt(i.get("title")), T.txt(i.get("description"))
            if t < cut or NOISE.search(title):
                continue
            show = show_of(title) or ("유튜브" if re.search(r"유튜브|채널", title) and COOK.search(title) else None)
            # 방송 이름 + 요리 이야기, 또는 제목에 레시피·만드는 법이 있고 방송 이야기인 것
            if not ((show and COOK.search(title)) or (STRONG.search(title) and (show or re.search(r"방송|예능|공개|셰프|유튜브|채널|SNS|인스타", title + desc)))):
                continue
            k = re.sub(r"[^가-힣a-zA-Z0-9]", "", title)[:26]
            if k in got:
                continue
            src = (re.search(r"//([^/]+)", i.get("originallink") or "") or [None, ""])[1]
            got[k] = {"title": title, "link": i.get("originallink") or i.get("link"), "t": t, "show": show,
                      "by": re.sub(r"^(?:www|m|news)\.", "", src)}
        time.sleep(0.1)
    rows = sorted(got.values(), key=lambda a: a["t"], reverse=True)
    T.save(OUT, {"updated": T.NOW.isoformat(), "ended": ended, "articles": rows[:200]})

    seen = T.load(SEEN, None)
    first = seen is None
    seen = seen or []
    new = [a for k, a in got.items() if k not in seen]
    test = os.getenv("RECIPE_TEST") == "1"
    send = rows[:5] if test else sorted(new, key=lambda a: (a["show"] is None, a["t"]))[:10]
    if send and (test or not first):
        lines = [("🧪 [테스트] " if test else "") + f"🍳 <b>방송 레시피 {len(send)}건</b>"
                 + (f"\n(방금 끝난 방송: {', '.join(ended)})" if ended else "")]
        for a in send:
            lines.append(f"\n• {'[' + html.escape(a['show']) + '] ' if a['show'] else ''}"
                         f"<a href=\"{html.escape(a['link'])}\">{html.escape(a['title'])}</a>")
        try:
            M.telegram("".join(lines))
        except Exception as e:
            print(f"::warning::레시피 알림 실패: {e}")
    T.save(SEEN, (seen + [k for k in got if k not in seen])[-3000:])
    print(f"[OK] 레시피: {len(rows)}건 (새 {len(new)}건, 방금 끝난 요리 방송 {ended})")
