"""
쇼핑 탭 수집 (네이버 API HUB · 쇼핑인사이트 + 블로그/뉴스 검색)
  1) 분야별 쇼핑 클릭 추이 (최근 30일)
  2) 분야 안 상품 키워드 클릭 추이 → 인기도·상승률
  3) 블로그 글 수 → 리뷰 경쟁도 (쇼핑 커넥트용 '쓰기 좋은' 상품 찾기)
결과: data/shopping/YYYY-MM-DD.json (하루 1번)
"""
import datetime as dt
import json
import math
import time

import trends as T
import hub as H

VERSION = 2
SHOPDIR = T.DATA / "shopping"
CONF = T.ROOT / "config" / "shopping.json"
PROBE = T.DATA / "shopping_probe.json"
PREFIXES = ["/shopping-insight/v1", "/shopping-insight/v1/shopping", "/v1/datalab/shopping",
            "/datalab/v1/shopping", "/shopping/v1"]


def post(path, body):
    r = T.S.post(H.BASE + path, data=json.dumps(body, ensure_ascii=False).encode(), headers=H.headers(), timeout=20)
    return r


def find_prefix(start, end):
    saved = T.load(PROBE, {})
    if saved.get("prefix"):
        return saved["prefix"]
    body = {"startDate": str(start), "endDate": str(end), "timeUnit": "date",
            "category": [{"name": "식품", "param": ["50000006"]}]}
    log = {}
    for p in PREFIXES:
        try:
            r = post(p + "/categories", body)
            log[p] = [r.status_code, r.text[:160]]
            if r.status_code == 200:
                T.save(PROBE, {"prefix": p, "checked": T.NOW.isoformat(), "log": log})
                return p
        except Exception as e:
            log[p] = ["ERR", str(e)[:160]]
    T.save(PROBE, {"prefix": None, "checked": T.NOW.isoformat(), "log": log})
    return None


def series_of(result, days):
    m = {d["period"]: d["ratio"] for d in result.get("data", [])}
    return [m.get(d, 0.0) for d in days]


def pct(now, base):
    return round((now - base) / base * 100) if base > 0.5 else (100 if now > 0.5 else 0)


def score(s):
    """s 마지막 값 = 어제(확정치). d1 = 전날 대비, w1 = 지난주 7일 평균 대비"""
    pop = round(sum(s[-7:]) / 7, 1)
    d1 = pct(s[-1], s[-2])
    w1 = pct(s[-1], sum(s[-8:-1]) / 7)
    return pop, d1, w1


def collect():
    if not __import__("os").getenv("NAVER_HUB_ID"):
        return
    f = SHOPDIR / f"{T.TODAY}.json"
    end = T.TODAY - dt.timedelta(days=1)
    old = T.load(f, {}) if f.exists() else {}
    if old.get("ver") == VERSION and old.get("range", [None, None])[1] == str(end):
        return                                      # 어제 확정치까지 이미 반영됨
    start = end - dt.timedelta(days=29)
    days = [str(start + dt.timedelta(days=i)) for i in range(30)]
    prefix = find_prefix(start, end)
    if not prefix:
        raise RuntimeError("쇼핑인사이트 API 주소를 찾지 못함 (data/shopping_probe.json 참고)")
    H.probe()                                   # 블로그·뉴스 검색 주소
    cfg = T.load(CONF, {"categories": {}})["categories"]

    # 1) 분야별 클릭 추이 (한 번에 최대 3분야 → 첫 분야를 기준으로 크기 맞춤)
    names = list(cfg)
    anchor = names[0]
    cat_series, base = {}, None
    rest = names[1:]
    for b in [rest[i:i + 2] for i in range(0, len(rest), 2)]:
        grp = [anchor] + b
        body = {"startDate": str(start), "endDate": str(end), "timeUnit": "date",
                "category": [{"name": n, "param": [cfg[n]["code"]]} for n in grp]}
        r = post(prefix + "/categories", body)
        r.raise_for_status()
        res = {x["title"]: series_of(x, days) for x in r.json().get("results", [])}
        a = res.get(anchor, [0]); am = sum(a) / len(a) or 1
        if base is None:
            base = am; cat_series[anchor] = a
        for n in b:
            if n in res:
                cat_series[n] = [v * base / am for v in res[n]]
        time.sleep(0.15)
    # 어제 자료가 아직 안 올라왔으면(전부 0) 그 날은 빼고 계산 → 다음 시간에 다시 시도
    nd = len(days)
    tot = lambda i: sum(s[i] for s in cat_series.values() if len(s) > i)
    while nd > 10 and (tot(nd - 1) < 0.5 * tot(nd - 2) or any(len(s) >= nd and s[nd - 1] == 0 for s in cat_series.values())):
        nd -= 1
    days = days[:nd]
    cat_series = {k: v[:nd] for k, v in cat_series.items()}
    if old.get("ver") == VERSION and old.get("range", [None, None])[1] == days[-1]:
        print(f"  (쇼핑: {end} 자료가 아직 없어요, 다음에 다시 시도)")
        return
    top = max((max(s) for s in cat_series.values() if s), default=1) or 1
    cats = []
    for n, s in cat_series.items():
        s = [round(v / top * 100, 1) for v in s]
        pop, d1, w1 = score(s)
        cats.append({"name": n, "code": cfg[n]["code"], "pop": pop, "rise": d1, "d1": d1, "w1": w1, "spark": s[-14:]})
    cats.sort(key=lambda c: -c["pop"])

    # 2) 분야별 상품 키워드 클릭 추이 (한 번에 최대 5개 → 첫 키워드 기준으로 맞춤)
    groups = []
    for n, c in cfg.items():
        seeds = [k for k in (H.clean_kw(x) for x in c.get("seeds", [])[:10]) if k]
        hot = [w for w in H.news_terms(c.get("news", []), seeds, limit=4) if H.clean_kw(w)]
        kws = seeds + [w for w in hot if w not in seeds]
        if not kws:
            continue
        a0, others = kws[0], kws[1:]
        ser, base = {}, None
        for b in [others[i:i + 4] for i in range(0, len(others), 4)] or [[]]:
            grp = [a0] + b
            body = {"startDate": str(start), "endDate": str(end), "timeUnit": "date", "category": c["code"],
                    "keyword": [{"name": k, "param": [k]} for k in grp]}
            try:
                r = post(prefix + "/category/keywords", body)
                r.raise_for_status()
            except Exception as e:
                print(f"  (쇼핑 키워드 실패 {n} {b}: {e})")
                continue
            res = {x["title"]: series_of(x, days) for x in r.json().get("results", [])}
            a = res.get(a0, [0]); am = sum(a) / len(a) or 1
            if base is None:
                base = am; ser[a0] = a
            for k in b:
                if k in res:
                    ser[k] = [v * base / am for v in res[k]]
            time.sleep(0.15)
        if not ser:
            continue
        top = max((max(s) for s in ser.values()), default=1) or 1
        rows = []
        for k, s in ser.items():
            s = [round(v / top * 100, 1) for v in s]
            pop, d1, w1 = score(s)
            try:
                blog, _ = H.search_total("blog", f"{k} 후기")
            except Exception:
                blog = None
            time.sleep(0.05)
            opp = round(pop / math.log10((blog or 0) + 10), 2) if pop else 0
            rows.append({"keyword": k, "pop": pop, "rise": d1, "d1": d1, "w1": w1, "spark": s[-14:], "blog": blog,
                         "opp": opp, "hot": k in hot and k not in seeds})
        rows.sort(key=lambda r: -r["pop"])
        groups.append({"name": n, "code": c["code"], "keywords": rows})

    opps = sorted(r["opp"] for g in groups for r in g["keywords"])
    if opps:
        lo, hi = opps[len(opps) // 3], opps[(2 * len(opps)) // 3]
        for g in groups:
            for r in g["keywords"]:
                r["grade"] = "good" if r["opp"] >= hi else "hard" if r["opp"] < lo else "mid"
    T.save(f, {"date": str(T.TODAY), "ver": VERSION, "updated": T.NOW.isoformat(),
               "range": [str(start), days[-1]], "categories": cats, "groups": groups})
    T.save(SHOPDIR / "index.json", [p.stem for p in sorted(SHOPDIR.glob("????-??-??.json"), reverse=True)[:60]])
    print(f"[OK] 쇼핑: 분야 {len(cats)}개, 키워드 {sum(len(g['keywords']) for g in groups)}개")
