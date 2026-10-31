"""
건강 · IT 신제품 기사 수집 (매시간, 네이버 뉴스)
결과: data/health/YYYY-MM-DD.json, data/it/YYYY-MM-DD.json (+ index.json)
"""
import datetime as dt
import os
import re
import time

import trends as T
import hub as H

SETS = {
    "health": [("질병·감염병", ["독감 유행", "감염병 주의", "질병관리청"]),
               ("영양제·식품", ["영양제 효능", "건강기능식품", "건강에 좋은 음식"]),
               ("운동·다이어트", ["운동 효과 연구", "다이어트 방법"]),
               ("제철 건강", ["환절기 건강", "겨울철 건강 관리"]),
               ("의료·병원", ["건강보험 적용", "병원 진료 변경", "의대 연구 결과"]),
               ("마음 건강", ["우울증", "수면 건강"])],
    "it": [("스마트폰", ["갤럭시 출시", "아이폰 출시", "스마트폰 신제품"]),
           ("노트북·태블릿", ["노트북 신제품", "아이패드 출시", "태블릿 출시"]),
           ("가전", ["가전 신제품 출시", "로봇청소기 신제품"]),
           ("AI·앱", ["AI 서비스 출시", "챗GPT 업데이트", "앱 업데이트 기능"]),
           ("게임", ["신작 게임 출시"]),
           ("통신·요금", ["요금제 출시", "통신사 혜택"])],
}
NOISE = re.compile(r"주가|목표주가|증시|배당|채용|인사\]|부고|\[포토\]")


def norm(t):
    return re.sub(r"[^가-힣a-zA-Z0-9]", "", t)[:26]


def news(q, n=30):
    _, path = H.ROUTES["news"]
    r = T.S.get(H.BASE + path, params={"query": q, "display": n, "sort": "date"}, headers=H.headers(), timeout=20)
    r.raise_for_status()
    out = []
    for i in r.json().get("items", []):
        try:
            t = dt.datetime.strptime(i["pubDate"], "%a, %d %b %Y %H:%M:%S %z")
        except Exception:
            continue
        src = (re.search(r"//([^/]+)", i.get("originallink") or "") or [None, ""])[1]
        out.append({"title": T.txt(i.get("title")), "link": i.get("originallink") or i.get("link"),
                    "t": t.isoformat(), "by": re.sub(r"^(?:www|m|news)\.", "", src)})
    return out


def collect():
    if not os.getenv("NAVER_HUB_ID"):
        return
    H.probe()
    for name, topics in SETS.items():
        d = T.DATA / name
        d.mkdir(parents=True, exist_ok=True)
        got = {}
        for tag, qs in topics:
            for q in qs:
                try:
                    for a in news(q):
                        if NOISE.search(a["title"]):
                            continue
                        k = norm(a["title"])
                        if k in got:
                            if tag not in got[k]["tags"]:
                                got[k]["tags"].append(tag)
                        else:
                            a["tags"] = [tag]
                            got[k] = a
                except Exception as e:
                    print(f"  ({name} 검색 실패 {q}: {e})")
                time.sleep(0.1)
        cut = str(T.TODAY - dt.timedelta(days=3))
        by_day = {}
        for a in got.values():
            if a["t"][:10] >= cut:
                by_day.setdefault(a["t"][:10], []).append(a)
        for day, items in by_day.items():
            f = d / f"{day}.json"
            book = {norm(x["title"]): x for x in T.load(f, {"articles": []})["articles"]}
            for a in items:
                k = norm(a["title"])
                if k in book:
                    book[k]["tags"] = sorted(set(book[k]["tags"]) | set(a["tags"]))
                else:
                    book[k] = a
            T.save(f, {"date": day, "updated": T.NOW.isoformat(),
                       "articles": sorted(book.values(), key=lambda x: x["t"], reverse=True)[:400]})
        files = sorted(d.glob("????-??-??.json"), reverse=True)
        for old in files[10:]:
            old.unlink()
        T.save(d / "index.json", [{"date": p.stem, "count": len(T.load(p, {"articles": []})["articles"])} for p in files[:10]])
        print(f"[OK] {name}: {len(got)}건")
