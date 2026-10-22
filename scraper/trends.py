"""
오늘의 소재 수집기 (매시간 실행)
  1) 연예 실시간 랭킹  : 네이트 연예 조회순·관심순 → 같은 이야기끼리 묶고, 순위 흐름 기록
  2) 시청률           : 닐슨코리아 전날 TOP20 (지상파·종편·케이블)
  3) 경기 일정        : KBO 공식 일정·결과
  4) 키워드 검색량     : 네이버 검색광고 API (키가 있을 때만, 하루 1번)

결과
  data/trends/ent_YYYY-MM-DD.json   연예 실시간 (그날 등장한 소재 전체)
  data/trends/index.json            날짜 목록
  data/ratings.json                 최근 시청률 (프로그램명 → 시청률)
  data/sports.json                  경기 일정
  data/keywords/YYYY-MM-DD.json     키워드 + 월간 검색량
"""
import base64
import datetime as dt
import hashlib
import hmac
import html
import json
import os
import pathlib
import re
import time
import traceback

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

KST = dt.timezone(dt.timedelta(hours=9))
ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
TRENDS = DATA / "trends"
KWDIR = DATA / "keywords"
CONFIG = ROOT / "config" / "keywords.json"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
S = requests.Session()
S.headers["User-Agent"] = UA
S.mount("https://", HTTPAdapter(max_retries=Retry(
    total=3, connect=3, read=2, backoff_factor=2,
    status_forcelist=(429, 500, 502, 503, 504), allowed_methods=("GET", "POST"))))

NOW = dt.datetime.now(KST)
TODAY = NOW.date()


def txt(s):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s or ""))).strip()


def load(p, default):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return default


def save(p, obj):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")


# =====================================================================
# 1) 연예 실시간 랭킹
# =====================================================================
RANK_SOURCES = {
    "nate_pop": ("네이트 많이 본", "https://news.nate.com/rank/pop?sc=ent&p=day&date={d}"),
    "nate_int": ("네이트 관심", "https://news.nate.com/rank/interest?sc=ent&p=day&date={d}"),
}


def nate_rank(url):
    r = S.get(url, timeout=20, headers={"Referer": "https://news.nate.com/rank/"})
    r.raise_for_status()
    page = r.content.decode("euc-kr", errors="replace")
    out = []
    for blk in page.split('class="mduRank rank')[1:]:
        m = re.match(r"(\d+)", blk)
        v = re.search(r'href="(?:https?:)?//news\.nate\.com/view/(\w+)', blk)
        if not (m and v):
            continue
        t = re.search(r'<h2 class="tit">(.*?)</h2>', blk, re.S) or \
            re.search(r'/view/\w+[^"]*"[^>]*>(.*?)</a>', blk, re.S)
        med = re.search(r'<span class="medium">(.*?)(?:<em>|</span>)', blk, re.S)
        title = txt(t.group(1)) if t else ""
        if title:
            out.append({"rank": int(m.group(1)), "title": title,
                        "link": f"https://news.nate.com/view/{v.group(1)}",
                        "media": txt(med.group(1)) if med else ""})
    seen, uniq = set(), []
    for a in sorted(out, key=lambda a: a["rank"]):
        if a["link"] not in seen:
            seen.add(a["link"])
            uniq.append(a)
    if len(uniq) < 10:
        raise ValueError(f"랭킹 {len(uniq)}개만 찾음 (페이지 구조 변경 가능성)")
    return uniq[:50]


STOP = set("""기자 단독 종합 포토 영상 공식 근황 충격 화제 논란 결국 최근 오늘 어제 이유 소식 공개 발표
그리고 하지만 이제 지금 진짜 정말 무슨 이게 그녀 그는 이후 앞서 위해 대한 통해 관련 이번 첫 또 더 HD 포토""".split())


def tokens(title):
    t = re.sub(r"\[[^\]]*\]|\([^)]*\)|<[^>]*>", " ", title)
    t = re.sub(r"[^\w가-힣]+", " ", t)
    out = set()
    for w in t.split():
        w = re.sub(r"(은|는|이|가|을|를|의|에|와|과|도|로|으로|에게|까지|부터|만|께|이랑|랑)$", "", w)
        if len(w) >= 2 and w not in STOP and not w.isdigit():
            out.add(w)
    return out


def bigrams(title):
    t = re.sub(r"[^가-힣a-zA-Z0-9]", "", title)
    return {t[i:i + 2] for i in range(len(t) - 1)}


def same_story(a, b):
    ta, tb = tokens(a), tokens(b)
    shared = ta & tb
    if len(shared) >= 2:
        return True
    ba, bb = bigrams(a), bigrams(b)
    ov = len(ba & bb) / min(len(ba), len(bb)) if ba and bb else 0
    if ov >= 0.45:
        return True
    # 이름처럼 긴 고유 단어 1개 + 비슷한 표현이 조금 더 겹치면 같은 이야기
    if any(len(w) >= 3 for w in shared) and ov >= 0.22:
        return True
    return False


def collect_ent():
    f = TRENDS / f"ent_{TODAY}.json"
    day = load(f, {"date": str(TODAY), "clusters": [], "snapshots": []})
    hour = NOW.strftime("%H:%M")
    now_items = []
    ok_sources = []
    for key, (label, url) in RANK_SOURCES.items():
        try:
            for a in nate_rank(url.format(d=TODAY.strftime("%Y%m%d"))):
                a["src"] = key
                now_items.append(a)
            ok_sources.append(key)
        except Exception as e:
            print(f"[실패] {label}: {e}")
    if not now_items:
        raise RuntimeError("연예 랭킹을 하나도 가져오지 못함")

    clusters = day["clusters"]
    for c in clusters:
        c["now"] = {}                      # 이번 시각 순위 초기화
    for a in now_items:
        target = None
        for c in clusters:
            if a["link"] in c["links"] or any(same_story(a["title"], t) for t in c["titles"][:6]):
                target = c
                break
        if target is None:
            target = {"id": f"c{len(clusters) + 1}", "title": a["title"], "titles": [], "links": [],
                      "articles": [], "first": hour, "last": hour, "best": 999, "hours": [],
                      "srcs": [], "now": {}}
            clusters.append(target)
        if a["link"] not in target["links"]:
            target["links"].append(a["link"])
            target["titles"].append(a["title"])
            target["articles"].append({"title": a["title"], "link": a["link"], "media": a["media"]})
        prev = target["now"].get(a["src"])
        target["now"][a["src"]] = min(prev, a["rank"]) if prev else a["rank"]
        target["best"] = min(target["best"], a["rank"])
        target["last"] = hour
        if a["src"] not in target["srcs"]:
            target["srcs"].append(a["src"])
        if hour not in target["hours"]:
            target["hours"].append(hour)

    # 대표 제목: 가장 높은 순위였던 기사 제목
    for c in clusters:
        c["count"] = len(c["articles"])
        c["inrank"] = bool(c["now"])
    day["snapshots"].append({"time": hour, "sources": ok_sources, "items": len(now_items)})
    day["updated"] = NOW.isoformat()
    day["labels"] = {k: v[0] for k, v in RANK_SOURCES.items()}
    save(f, day)

    idx = []
    for p in sorted(TRENDS.glob("ent_????-??-??.json"), reverse=True)[:60]:
        d = load(p, {})
        idx.append({"date": p.stem[4:], "count": len(d.get("clusters", []))})
    save(TRENDS / "index.json", idx)
    print(f"[OK] 연예 랭킹: 기사 {len(now_items)}개 → 소재 {len(clusters)}개")


# =====================================================================
# 2) 시청률 (닐슨코리아 전날 TOP20)
# =====================================================================
NIELSEN = {"1_1": "지상파", "2_1": "종편", "3_1": "케이블"}


def norm_prog(s):
    s = re.sub(r"<[^>]*>|\[[^\]]*\]", "", s)
    s = re.sub(r"\s|\(재\)|\(본\)|[^\w가-힣]", "", s)
    return s.lower()


def collect_ratings():
    out = load(DATA / "ratings.json", {"days": {}})
    for back in (1, 2):                      # 전날 자료가 아직 없으면 그 전날
        d = TODAY - dt.timedelta(days=back)
        if str(d) in out["days"]:
            continue
        rows = []
        for sub, kind in NIELSEN.items():
            url = (f"https://www.nielsenkorea.co.kr/tv_terrestrial_day.asp?menu=Tit_1&sub_menu={sub}"
                   f"&area=00&sYear={d:%Y}&sMonth={d:%m}&sDay={d:%d}")
            r = S.get(url, timeout=20)
            r.raise_for_status()
            page = r.text
            if f"{d:%Y.%m.%d}" not in page:
                break
            body = page.split("가구시청률")[1] if "가구시청률" in page else page
            body = body.split("시청자수 TOP")[0]
            for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", body, re.S):
                tds = [txt(x) for x in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)]
                if len(tds) == 4 and tds[0].isdigit():
                    try:
                        rows.append({"rank": int(tds[0]), "kind": kind, "channel": tds[1],
                                     "program": tds[2], "rating": float(tds[3])})
                    except ValueError:
                        pass
        if rows:
            out["days"][str(d)] = rows
            print(f"[OK] 시청률 {d}: {len(rows)}개")
    keep = sorted(out["days"])[-14:]
    out["days"] = {k: out["days"][k] for k in keep}
    # 프로그램별 최근 시청률 사전 (화면에서 편성표와 이어 붙임)
    latest = {}
    for d in keep:
        for r in out["days"][d]:
            inner = re.search(r"\(([^)]+)\)", r["program"])
            name = inner.group(1) if inner and len(inner.group(1)) >= 2 else r["program"]
            latest[norm_prog(name)] = {"rating": r["rating"], "date": d, "channel": r["channel"],
                                       "program": r["program"], "kind": r["kind"]}
    out["latest"] = latest
    out["updated"] = NOW.isoformat()
    save(DATA / "ratings.json", out)


# =====================================================================
# 3) 경기 일정 (KBO)
# =====================================================================
def kbo_month(year, month):
    r = S.post("https://www.koreabaseball.com/ws/Schedule.asmx/GetScheduleList", timeout=20,
               data={"leId": "1", "srIdList": "0,1,3,4,5,6,7,8,9", "seasonId": str(year),
                     "gameMonth": f"{month:02d}", "teamId": ""},
               headers={"Referer": "https://www.koreabaseball.com/Schedule/Schedule.aspx",
                        "X-Requested-With": "XMLHttpRequest"})
    r.raise_for_status()
    games, cur_day = [], None
    for row in r.json().get("rows", []):
        cells = row["row"]
        texts = [c.get("Text") or "" for c in cells]
        classes = [c.get("Class") or "" for c in cells]
        if "day" in classes:
            cur_day = txt(texts[classes.index("day")])
        if "play" not in classes or not cur_day:
            continue
        i = classes.index("play")
        play = texts[i]
        time_s = txt(texts[classes.index("time")]) if "time" in classes else ""
        teams = re.findall(r"<span[^>]*>([^<]*[가-힣A-Za-z][^<]*)</span>", play)
        teams = [t for t in teams if t != "vs"]
        scores = re.findall(r"<span[^>]*>(\d+)</span>", play)
        rest = [txt(t) for t in texts[i + 1:]]
        m = re.match(r"(\d+)\.(\d+)", cur_day)
        if not (m and len(teams) >= 2 and re.match(r"\d+:\d+", time_s)):
            continue
        tv = rest[2] if len(rest) > 2 else ""
        place = rest[4] if len(rest) > 4 else ""
        note = rest[5] if len(rest) > 5 and rest[5] != "-" else ""
        d = dt.date(year, int(m.group(1)), int(m.group(2)))
        hh, mm = map(int, time_s.split(":"))
        start = dt.datetime(d.year, d.month, d.day, hh, mm, tzinfo=KST)
        status = "예정"
        if re.search(r"btnReview|>\s*리뷰\s*<", "".join(texts)):
            status = "종료"
        elif len(scores) >= 2 and start <= NOW:
            status = "진행"
        games.append({"date": str(d), "time": time_s, "league": "KBO",
                      "away": teams[0], "home": teams[1],
                      "score": f"{scores[0]}:{scores[1]}" if len(scores) >= 2 and status != "예정" else "",
                      "status": status, "tv": tv, "place": place, "note": note})
    return games


def collect_sports():
    games = []
    months = {(TODAY.year, TODAY.month), ((TODAY + dt.timedelta(days=10)).year,
                                          (TODAY + dt.timedelta(days=10)).month)}
    for y, mth in sorted(months):
        games += kbo_month(y, mth)
    lo, hi = str(TODAY - dt.timedelta(days=3)), str(TODAY + dt.timedelta(days=10))
    games = [g for g in games if lo <= g["date"] <= hi]
    save(DATA / "sports.json", {"updated": NOW.isoformat(), "games": games})
    print(f"[OK] 경기 일정: {len(games)}개")


# =====================================================================
# 4) 키워드 + 월간 검색량 (네이버 검색광고 API, 하루 1번)
# =====================================================================
def ad_get(path, params):
    key, secret, cust = (os.getenv("NAVER_AD_API_KEY"), os.getenv("NAVER_AD_SECRET"),
                         os.getenv("NAVER_AD_CUSTOMER_ID"))
    ts = str(int(time.time() * 1000))
    sig = base64.b64encode(hmac.new(secret.encode(), f"{ts}.GET.{path}".encode(),
                                    hashlib.sha256).digest()).decode()
    r = S.get("https://api.searchad.naver.com" + path, params=params, timeout=20, headers={
        "X-Timestamp": ts, "X-API-KEY": key, "X-Customer": str(cust), "X-Signature": sig})
    r.raise_for_status()
    return r.json()


def qc(v):
    if isinstance(v, (int, float)):
        return int(v)
    return 5 if "<" in str(v) else int(re.sub(r"\D", "", str(v)) or 0)


def program_seeds():
    """오늘 방송되는 본방 프로그램 이름 (시청률 높은 순)."""
    sched = load(DATA / "schedule.json", {"channels": []})
    ratings = load(DATA / "ratings.json", {}).get("latest", {})
    names = {}
    for ch in sched["channels"]:
        for p in ch["programs"]:
            if not p["start"].startswith(str(TODAY)) or p.get("type") == "재":
                continue
            name = re.sub(r"\s*\d+\s*[회화부]$|\s*\(.*?\)$|\s*스페셜$", "", p["title"]).strip()
            if len(name) < 2 or re.search(r"뉴스|날씨|광고|특선|재방", name):
                continue
            r = ratings.get(norm_prog(name), {}).get("rating", 0)
            names[name] = max(names.get(name, 0), r)
    return [n for n, _ in sorted(names.items(), key=lambda x: -x[1])][:25]


def ent_seeds():
    """오늘 연예 소재에서 인물·프로그램 이름 후보."""
    day = load(TRENDS / f"ent_{TODAY}.json", {"clusters": []})
    cnt = {}
    for c in sorted(day["clusters"], key=lambda c: c["best"])[:40]:
        for t in c["titles"][:3]:
            for q in re.findall(r"'([^']{2,12})'|\"([^\"]{2,12})\"", t):
                w = (q[0] or q[1]).strip()
                if re.fullmatch(r"[가-힣A-Za-z0-9]{2,8}", w):
                    cnt[w] = cnt.get(w, 0) + 1
            m = re.match(r"^\s*([가-힣A-Za-z]{2,6})[,·…♥]", re.sub(r"^\[[^\]]*\]\s*|^'[^']*'\s*|^\"[^\"]*\"…?\s*", "", t))
            if m:
                cnt[m.group(1)] = cnt.get(m.group(1), 0) + 1
    return [w for w, _ in sorted(cnt.items(), key=lambda x: -x[1])][:25]


def collect_keywords():
    if not os.getenv("NAVER_AD_API_KEY"):
        print("네이버 검색광고 API 키 없음 → 키워드 건너뜀")
        return
    f = KWDIR / f"{TODAY}.json"
    if f.exists() or NOW.hour < 6:           # 하루 1번, 오전 6시 이후 첫 실행 때
        return
    cfg = load(CONFIG, {"categories": {}})
    groups = {"오늘 방송 프로그램": program_seeds(), "오늘의 연예 소재": ent_seeds()}
    groups.update({k: v for k, v in cfg.get("categories", {}).items() if v})
    prev_f = KWDIR / f"{TODAY - dt.timedelta(days=1)}.json"
    prev = {}
    for g in load(prev_f, {}).get("groups", []):
        for i, k in enumerate(g["keywords"]):
            prev[(g["name"], k["keyword"])] = i + 1

    out = []
    for gname, seeds in groups.items():
        rows = {}
        for i in range(0, len(seeds), 5):
            hint = ",".join(re.sub(r"\s+", "", s) for s in seeds[i:i + 5] if s)
            if not hint:
                continue
            try:
                res = ad_get("/keywordstool", {"hintKeywords": hint, "showDetail": "1"})
            except Exception as e:
                print(f"[실패] 키워드 {gname} ({hint}): {e}")
                continue
            hints = set(hint.split(","))
            for k in res.get("keywordList", [])[:40]:
                kw = k["relKeyword"]
                pc, mo = qc(k.get("monthlyPcQcCnt")), qc(k.get("monthlyMobileQcCnt"))
                rows[kw] = {"keyword": kw, "pc": pc, "mobile": mo, "total": pc + mo,
                            "seed": kw in hints, "comp": k.get("compIdx", "")}
            time.sleep(0.3)
        kws = sorted(rows.values(), key=lambda r: -r["total"])[:30]
        for i, k in enumerate(kws):
            p = prev.get((gname, k["keyword"]))
            k["move"] = (p - (i + 1)) if p else None          # +면 상승, None이면 새로 등장
        out.append({"name": gname, "seeds": seeds, "keywords": kws})
    save(f, {"date": str(TODAY), "hour": NOW.hour, "updated": NOW.isoformat(), "groups": out})
    idx = [p.stem for p in sorted(KWDIR.glob("????-??-??.json"), reverse=True)[:60]]
    save(KWDIR / "index.json", idx)
    print(f"[OK] 키워드: {sum(len(g['keywords']) for g in out)}개")


def _hub_collect():
    import sys
    sys.path.insert(0, str(pathlib.Path(__file__).parent))
    import hub
    hub.collect()


def _shop_collect():
    import sys
    sys.path.insert(0, str(pathlib.Path(__file__).parent))
    import shopping
    shopping.collect()


def main():
    failed = []
    for name, fn in [("연예 랭킹", collect_ent), ("시청률", collect_ratings),
                     ("경기 일정", collect_sports), ("키워드", collect_keywords),
                     ("키워드(API HUB)", _hub_collect), ("쇼핑", _shop_collect)]:
        try:
            fn()
        except Exception as e:
            failed.append(name)
            print(f"[실패] {name}: {e}")
            traceback.print_exc()
    if failed:
        print("::warning::소재 수집 실패: " + ", ".join(failed))


if __name__ == "__main__":
    main()
