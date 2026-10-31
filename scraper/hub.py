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
VERSION = 5          # 수집 방식을 바꾸면 숫자를 올림 → 오늘 자료를 새로 만듦
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


NEWS_STOP = set("""뉴스 기자 단독 속보 오늘 내일 어제 올해 지난 이번 최대 최고 최초 공개 출시 발표 진행 개최 확대 지원
시작 예정 관련 위해 대해 통해 이후 이상 이하 가능 제공 운영 선정 추진 강화 기념 맞이 대상 결과 가운데 정부 국내 세계 한국
서울 전국 지역 시장 업계 기업 회사 사업 행사 신규 첫날 이벤트 할인 혜택 사진 영상 포토 종합 인터뷰 화제 인기 논란
대표 국가대표 대회 개막 폐막 상승 하락 급등 급락 유일 내년 주목 전자 상무 이사 회장 사장 경기 설명회 관리 예방 질병 교수
표창 수상 휴직 셋째 둘째 장려금 지인 페스티벌 신제품 제품 전용 구축 국립 시립 영화 드라마 시청률 연말 연초 글씨 게임
공모전 중독 판매량 트렌드 패션 뷰티 신상 축구 야구 농구 배구 여행 맛집 건강 육아 결혼 반려 원예 음악 공연 취미 교육
대입 입시 문화센터 시청 구청 군청 도청 협약 체결 모집 접수 신청 성료 마련 확인 기록 돌파 달성 증가 감소 전망 분석
남양주시 인천시 부산시 경기도 미국 중국 일본 뉴욕 정책 방안 계획 조사 발표회 축제 박람회 전시 시즌 오픈 론칭 출범""".split())
BAD_END = re.compile(r"(할|한|된|될|는|던|서|에서|으로|까지|부터|처럼|하고|하는|했다|이다|에게|께서|이며|이고)$")


def news_terms(queries, exclude, limit=6):
    """분야 뉴스 최신 기사 제목에서 자주 나온 단어 → 그날 뜨는 키워드 후보."""
    if "news" not in ROUTES:
        return []
    cnt = {}
    for q in queries:
        try:
            _, items = search_total("news", q, display=50)
        except Exception as e:
            print(f"  (뉴스 실패 {q}: {e})")
            continue
        for it in items:
            for w in T.tokens(T.txt(it.get("title", ""))):
                if (w in NEWS_STOP or len(w) > 10 or len(w) < 2 or BAD_END.search(w)
                        or re.fullmatch(r"[A-Za-z]{1,2}|\d+\S*|.*(시|군|구|청)$", w) and len(w) <= 4 and w not in exclude):
                    continue
                cnt[w] = cnt.get(w, 0) + 1
        time.sleep(0.1)
    low = {e.replace(" ", "").lower() for e in exclude} | {q.replace(" ", "").lower() for q in queries}
    picks = [w for w, c in sorted(cnt.items(), key=lambda x: -x[1]) if c >= 3 and w.lower() not in low
             and not any(w in x or x in w for x in low)]
    return picks[:limit]


def clean_kw(s):
    s = re.sub(r"\s+", " ", s).strip()
    return s if 2 <= len(s) <= 20 else None


def drama_seeds():
    """방영 중 드라마 제목 (텔레그램 드라마 알림과 같은 목록)"""
    try:
        import news
        return sorted(t for t in news.airing_dramas() if "-" not in t and len(t) <= 10)
    except Exception:
        return []


def collect():
    if not os.getenv("NAVER_HUB_ID"):
        print("NAVER API HUB 키 없음 → 키워드 건너뜀")
        return
    f = T.KWDIR / f"{T.TODAY}.json"
    if f.exists() and T.load(f, {}).get("ver") == VERSION:
        return                                            # 오늘 이미 수집
    probe()
    if "trend" not in ROUTES:
        raise RuntimeError("검색어트렌드 API 연결 실패 (data/hub_probe.json 참고)")

    cfg = T.load(T.CONFIG, {"categories": {}})
    groups, areas = {}, {}
    auto = set()
    for name, c in cfg.get("categories", {}).items():
        if isinstance(c, list):                       # 예전 형식: ["단어", ...]
            groups[name] = c[:12]
            continue
        areas[name] = c.get("area", "")
        seeds = c.get("seeds", [])[:10]
        if c.get("auto") == "program":
            seeds = (T.program_seeds() + seeds)[:10]
        elif c.get("auto") == "ent":
            seeds = (T.ent_seeds() + seeds)[:10]
        elif c.get("auto") == "drama":
            seeds = drama_seeds()[:10]
        hot = news_terms(c.get("news", []), seeds)
        auto |= {(name, w) for w in hot}
        groups[name] = seeds + hot
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

    # 2) 블로그 글 수(전체), 지식iN 질문(분야별 인기 상위 6개만 — 호출 수 절약)
    info = {kw: {"blog": None, "cafe": None, "questions": []} for kw in allkw}
    for kw in allkw:
        if kw not in series:
            continue
        try:
            info[kw]["blog"], _ = search_total("blog", kw)
        except Exception:
            pass
        time.sleep(0.05)
    want_q = set()
    for kws in groups.values():
        want_q |= set(sorted([k for k in kws if k in series], key=lambda k: -sum(series[k][-7:]))[:6])
    for kw in want_q:
        try:
            _, items = search_total("kin", kw, display=3)
            info[kw]["questions"] = [{"title": T.txt(i.get("title")), "link": i.get("link")} for i in items[:3]]
        except Exception:
            pass
        time.sleep(0.05)

    # 3) 점수: 인기도(최근 7일 평균), 상승률(최근 3일 vs 그 전 7일), 기회(인기도 ÷ 경쟁)
    prev = {}
    for g in T.load(T.KWDIR / f"{T.TODAY - dt.timedelta(days=1)}.json", {}).get("groups", []):
        for i, k in enumerate(g.get("keywords", [])):
            prev[(g["name"], k["keyword"])] = i + 1
    out = []
    for gname, kws in groups.items():
        rows = []
        gtop = max((max(series[k]) for k in kws if series.get(k)), default=0) or 1   # 주제 안에서 가장 많이 찾은 것 = 100
        for kw in kws:
            s = series.get(kw)
            if not s:
                continue
            s = [round(v / gtop * 100, 1) for v in s]
            pop = round(sum(s[-7:]) / 7, 1)
            recent, before = sum(s[-3:]) / 3, sum(s[-10:-3]) / 7
            rise = round((recent - before) / before * 100) if before > 0.5 else (100 if recent > 0.5 else 0)
            blog = info.get(kw, {}).get("blog")
            opp = round(pop / math.log10((blog or 0) + 10), 2) if pop else 0
            rows.append({"keyword": kw, "pop": pop, "rise": rise, "spark": s[-14:], "hot": (gname, kw) in auto,
                         "blog": blog, "cafe": info.get(kw, {}).get("cafe"),
                         "questions": info.get(kw, {}).get("questions", []), "opp": opp})
        rows.sort(key=lambda r: -r["pop"])
        for i, r in enumerate(rows):
            p = prev.get((gname, r["keyword"]))
            r["move"] = (p - (i + 1)) if p else None
        out.append({"name": gname, "area": areas.get(gname, ""), "keywords": rows})
    # 기회 등급: 전체에서 상위 1/3 = 쓰기 좋음, 하위 1/3 = 경쟁 심함
    opps = sorted(r["opp"] for g in out for r in g["keywords"])
    if opps:
        lo, hi = opps[len(opps) // 3], opps[(2 * len(opps)) // 3]
        for g in out:
            for r in g["keywords"]:
                r["grade"] = "good" if r["opp"] >= hi else "hard" if r["opp"] < lo else "mid"
    T.save(f, {"date": str(T.TODAY), "source": "hub", "ver": VERSION, "updated": T.NOW.isoformat(),
               "range": [str(start), str(end)], "groups": out})
    idx = [p.stem for p in sorted(T.KWDIR.glob("????-??-??.json"), reverse=True)[:60]]
    T.save(T.KWDIR / "index.json", idx)
    print(f"[OK] 키워드(API HUB): {sum(len(g['keywords']) for g in out)}개")
