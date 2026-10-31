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
    "health": [("효능·영양", ["효능", "영양 성분 효과", "건강에 좋은 음식", "먹으면 좋은"]),
               ("영양제", ["영양제 효과", "건강기능식품", "영양제 부작용", "비타민 효과"]),
               ("질환·증상", ["독감 증상", "감기 예방", "혈압 낮추는", "혈당 관리", "질환 예방"]),
               ("제철 건강", ["제철 음식 효능", "환절기 건강", "겨울철 건강"]),
               ("운동·다이어트", ["다이어트 효과", "운동 효과", "걷기 효과"]),
               ("연구 결과", ["섭취 연구 결과", "건강 연구 결과", "위험 낮춘다"])],
    "it": [("스마트폰", ["갤럭시 출시", "아이폰 출시", "스마트폰 신제품"]),
           ("노트북·태블릿", ["노트북 신제품", "아이패드 출시", "태블릿 출시"]),
           ("가전", ["가전 신제품 출시", "로봇청소기 신제품"]),
           ("AI·앱", ["AI 서비스 출시", "챗GPT 업데이트", "앱 업데이트 기능"]),
           ("게임", ["신작 게임 출시"]),
           ("통신·요금", ["요금제 출시", "통신사 혜택", "통신 장애"]),
           ("보안·개인정보", ["해킹 개인정보 유출", "보안 취약점"]),
           ("PC·부품", ["그래픽카드", "PC 부품 가격", "윈도우 업데이트"]),
           ("IT 업계", ["IT 업계 이슈", "반도체 업계", "네이버 카카오 서비스", "구글 애플 발표"])],
}
NOISE = re.compile(r"주가|목표주가|증시|배당|채용|인사\]|부고|\[포토\]")
HEALTH_OK = re.compile(r"효능|효과|건강|영양|섭취|음식|식품|질환|증상|예방|치료|혈압|혈당|당뇨|콜레스테롤|면역|다이어트|운동|수면|비타민|유산균|단백질|암|염증|감기|독감|부작용|연구|좋은|나쁜|위험|먹으면|먹는|마시면|영양제|칼로리|체중|노화")
HEALTH_BAD = re.compile(r"학폭|열애|결혼|이혼|배우|가수|아이돌|예능|드라마|컴백|주가|증시|매출|실적|선거|국회|축제|공연|전시")


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


def health_topics(d):
    """최근 3일 건강 기사 제목에서 '○○ 효능 / ○○ 부작용 / ○○에 좋은' 소재 뽑기 → health/topics.json"""
    terms = T.load(T.ROOT / "config" / "health_terms.json", {})
    kinds = {w: k for k, ws in terms.items() if not k.startswith("_") for w in ws}
    cut = (T.NOW - dt.timedelta(days=3)).isoformat()
    arts = []
    for p in sorted(d.glob("????-??-??.json"), reverse=True)[:4]:
        arts += [a for a in T.load(p, {"articles": []})["articles"] if a["t"] >= cut]
    ANG = [("효능", r"효능|효과|좋은 점|이점"), ("부작용", r"부작용|주의|위험|나쁜|해로"), ("에 좋은", r"에 좋은|좋은 음식|도움|낮추|높이|예방"),
           ("먹는 법", r"먹는 법|먹으면|섭취|드시|마시면|하루 \d")]
    found = {}
    for a in arts:
        t = a["title"]
        names = set()
        for w in kinds:
            if re.search(r"(?<![가-힣A-Za-z0-9])" + re.escape(w) + r"(?![가-힣A-Za-z])|(?<![가-힣A-Za-z0-9])" + re.escape(w) + r"(?=의|은|는|이|가|을|를|에|와|과|도|로|만|즙|차|물)", t):
                names.add(w)
        for m in re.finditer(r"([가-힣]{2,6})(?:의)? ?(?:효능|부작용)", t):
            g = m.group(1)
            if g not in ("건강", "음식", "식품", "영양제", "약물", "치료", "운동", "다이어트", "효과") and not re.search(r"[운한은는인된적]$", g):
                names.add(g)
        for n in names:
            f = found.setdefault(n, {"name": n, "kind": kinds.get(n, "기타"), "count": 0, "angles": {}, "items": []})
            f["count"] += 1
            for ang, pat in ANG:
                if re.search(pat, t):
                    f["angles"][ang] = f["angles"].get(ang, 0) + 1
            if len(f["items"]) < 5:
                f["items"].append({"title": t, "link": a["link"], "t": a["t"], "by": a.get("by", "")})
    rows = sorted(found.values(), key=lambda x: (-x["count"], x["name"]))[:30]
    for r in rows:
        r["angles"] = [k for k, _ in sorted(r["angles"].items(), key=lambda x: -x[1])]
    T.save(d / "topics.json", {"updated": T.NOW.isoformat(), "topics": rows})


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
                        if name == "health" and (HEALTH_BAD.search(a["title"]) or not HEALTH_OK.search(a["title"])):
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
        if name == "health":
            health_topics(d)
        else:
            import econ
            econ.save_issues(d, name)
        print(f"[OK] {name}: {len(got)}건")
