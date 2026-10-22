"""
네이버 API HUB(네이버 클라우드) 키워드 수집
  - 검색어트렌드: 최근 30일 인기도(0~100)와 오르내림
  - 블로그·카페 검색: 키워드별 글 수 → 경쟁도
  - 지식iN 검색: 사람들이 실제로 묻는 질문
결과: data/keywords/YYYY-MM-DD.json  (하루 1번, 오전 6시 이후 첫 실행. 오늘 파일이 없으면 바로 실행)

GitHub Secrets: NAVER_HUB_ID (Client ID), NAVER_HUB_SECRET (Client Secret)
"""
import datetime as dt
import json
import math
import os
import re
import time

import trends as T

BASE = "https://naverapihub.apigw.ntruss.com"
PROBE = T.DATA / "hub_probe.json"


def headers():
    return {"X-NCP-APIGW-API-KEY-ID": os.getenv("NAVER_HUB_ID", ""),
            "X-NCP-APIGW-API-KEY": os.getenv("NAVER_HUB_SECRET", ""),
            "Content-Type": "application/json"}


# ---------- 연결 확인 (키 값은 기록하지 않고 상태 코드만 남김) ----------
CANDIDATES = {
    "blog": [("GET", "/search/v1/blog"), ("GET", "/v1/search/blog.json"), ("GET", "/search/v1/blog.json")],
    "cafe": [("GET", "/search/v1/cafearticle"), ("GET", "/v1/search/cafearticle.json")],
    "kin": [("GET", "/search/v1/kin"), ("GET", "/v1/search/kin.json")],
    "news": [("GET", "/search/v1/news"), ("GET", "/v1/search/news.json")],
    "image": [("GET", "/search/v1/image"), ("GET", "/v1/search/image")],
    "trend": [("POST", "/search-trend/v1/search"), ("POST", "/v1/datalab/search"), ("POST", "/datalab/v1/search")],
}
ROUTES = {}


def probe():
    """각 API의 실제 주소를 찾아 기록 (처음 한 번 / 실패 시 다시)."""
    saved = T.load(PROBE, {})
    if saved.get("ok") and saved.get("routes"):
        ROUTES.update(saved["routes"])
        return
    end = T.TODAY - dt.timedelta(days=1)
    body = {"startDate": str(end - dt.timedelta(days=6)), "endDate": str(end), "timeUnit": "date",
            "keywordGroups": [{"groupName": "날씨", "keywords": ["날씨"]}]}
    log, routes = {}, {}
    for name, cands in CANDIDATES.items():
        for method, path in cands:
            try:
                if method == "GET":
                    r = T.S.get(BASE + path, params={"query": "날씨", "display": 1}, headers=headers(), timeout=20)
                else:
                    r = T.S.post(BASE + path, data=json.dumps(body), headers=headers(), timeout=20)
                log[f"{method} {path}"] = [r.status_code, r.text[:160]]
                if r.status_code == 200:
                    routes[name] = [method, path]
                    break
            except Exception as e:
                log[f"{method} {path}"] = ["ERR", str(e)[:160]]
    T.save(PROBE, {"checked": T.NOW.isoformat(), "ok": "trend" in routes and "blog" in routes,
                   "routes": routes, "log": log})
    ROUTES.update(routes)
    print("[API HUB 연결 확인]", {k: v[1] for k, v in routes.items()} or "실패 - data/hub_probe.json 확인")


def search_total(kind, q, display=1):
    if kind not in ROUTES:
        return None, []
    _, path = ROUTES[kind]
    r = T.S.get(BASE + path, params={"query": q, "display": display, "sort": "sim"}, headers=headers(), timeout=20)
    r.raise_for_status()
    j = r.json()
    return j.get("total"), j.get("items", [])


def trend(groups, start, end):
    """groups: [키워드, ...] 최대 5개 → {키워드: [일별 비율]}"""
    _, path = ROUTES["trend"]
    body = {"startDate": str(start), "endDate": str(end), "timeUnit": "date",
            "keywordGroups": [{"groupName": k, "keywords": [k]} for k in groups]}
    r = T.S.post(BASE + path, data=json.dumps(body, ensure_ascii=False).encode(), headers=headers(), timeout=20)
    r.raise_for_status()
    days = [str(start + dt.timedelta(days=i)) for i in range((end - start).days + 1)]
    out = {}
    for res in r.json().get("results", []):
        m = {d["period"]: d["ratio"] for d in res.get("data", [])}
        out[res["title"]] = [m.get(d, 0.0) for d in days]
    return out


def clean_kw(s):
    s = re.sub(r"\s+", " ", s).strip()
    return s if 2 <= len(s) <= 20 else None


def collect():
    if not os.getenv("NAVER_HUB_ID"):
        print("NAVER API HUB 키 없음 → 키워드 건너뜀")
        return
    f = T.KWDIR / f"{T.TODAY}.json"
    if f.exists() and T.load(f, {}).get("source") == "hub":
        return                                            # 오늘 이미 수집
    probe()
    if "trend" not in ROUTES:
        raise RuntimeError("검색어트렌드 API 연결 실패 (data/hub_probe.json 참고)")

    cfg = T.load(T.CONFIG, {"categories": {}})
    groups = {"오늘 방송 프로그램": T.program_seeds()[:15], "오늘의 연예 소재": T.ent_seeds()[:15]}
    groups.update({k: v[:10] for k, v in cfg.get("categories", {}).items() if v})
    allkw = []
    for g, ks in groups.items():
        groups[g] = [k for k in (clean_kw(x) for x in ks) if k]
        allkw += [k for k in groups[g] if k not in allkw]
    if not allkw:
        print("키워드 후보 없음")
        return

    # 1) 검색어트렌드: 기준 키워드(anchor)를 매번 함께 넣어 묶음 간 크기를 맞춤
    end = T.TODAY - dt.timedelta(days=1)
    start = end - dt.timedelta(days=29)
    anchor, rest = allkw[0], allkw[1:]
    series, base_mean = {}, None
    batches = [rest[i:i + 4] for i in range(0, len(rest), 4)] or [[]]
    for b in batches:
        try:
            res = trend([anchor] + b, start, end)
        except Exception as e:
            print(f"  (트렌드 실패 {b}: {e})")
            continue
        a = res.get(anchor) or [0]
        am = sum(a) / len(a)
        if base_mean is None:
            base_mean = am or 1.0
            series[anchor] = a
        k = (base_mean / am) if am else 1.0
        for kw in b:
            if kw in res:
                series[kw] = [v * k for v in res[kw]]
        time.sleep(0.15)
    top = max((max(s) for s in series.values() if s), default=1) or 1
    for kw in series:
        series[kw] = [round(v / top * 100, 1) for v in series[kw]]

    # 2) 블로그·카페 글 수, 지식iN 질문
    info = {}
    for kw in allkw:
        row = {}
        for kind in ("blog", "cafe"):
            try:
                row[kind], _ = search_total(kind, kw)
            except Exception:
                row[kind] = None
        try:
            _, items = search_total("kin", kw, display=3)
            row["questions"] = [{"title": T.txt(i.get("title")), "link": i.get("link")} for i in items[:3]]
        except Exception:
            row["questions"] = []
        info[kw] = row
        time.sleep(0.1)

    # 3) 점수: 인기도(최근 7일 평균), 상승률(최근 3일 vs 그 전 7일), 기회(인기도 ÷ 경쟁)
    prev = {}
    for g in T.load(T.KWDIR / f"{T.TODAY - dt.timedelta(days=1)}.json", {}).get("groups", []):
        for i, k in enumerate(g.get("keywords", [])):
            prev[(g["name"], k["keyword"])] = i + 1
    out = []
    for gname, kws in groups.items():
        rows = []
        for kw in kws:
            s = series.get(kw)
            if not s:
                continue
            pop = round(sum(s[-7:]) / 7, 1)
            recent, before = sum(s[-3:]) / 3, sum(s[-10:-3]) / 7
            rise = round((recent - before) / before * 100) if before > 0.5 else (100 if recent > 0.5 else 0)
            blog = info.get(kw, {}).get("blog")
            opp = round(pop / math.log10((blog or 0) + 10), 2) if pop else 0
            rows.append({"keyword": kw, "pop": pop, "rise": rise, "spark": s[-14:],
                         "blog": blog, "cafe": info.get(kw, {}).get("cafe"),
                         "questions": info.get(kw, {}).get("questions", []), "opp": opp})
        rows.sort(key=lambda r: -r["pop"])
        for i, r in enumerate(rows):
            p = prev.get((gname, r["keyword"]))
            r["move"] = (p - (i + 1)) if p else None
        out.append({"name": gname, "keywords": rows})
    # 기회 등급: 전체에서 상위 1/3 = 쓰기 좋음, 하위 1/3 = 경쟁 심함
    opps = sorted(r["opp"] for g in out for r in g["keywords"])
    if opps:
        lo, hi = opps[len(opps) // 3], opps[(2 * len(opps)) // 3]
        for g in out:
            for r in g["keywords"]:
                r["grade"] = "good" if r["opp"] >= hi else "hard" if r["opp"] < lo else "mid"
    T.save(f, {"date": str(T.TODAY), "source": "hub", "updated": T.NOW.isoformat(),
               "range": [str(start), str(end)], "groups": out})
    idx = [p.stem for p in sorted(T.KWDIR.glob("????-??-??.json"), reverse=True)[:60]]
    T.save(T.KWDIR / "index.json", idx)
    print(f"[OK] 키워드(API HUB): {sum(len(g['keywords']) for g in out)}개")
