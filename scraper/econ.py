"""
경제·정책 탭 수집 (매시간)
  1) 대한민국 정책브리핑(korea.kr) 정책뉴스·보도자료·사실은 이렇습니다 목록 페이지
  2) 네이버 뉴스 검색: 분야별 경제 뉴스 (+ 정책브리핑이 막히면 정책 기사로 대신)
  3) 신청·접수 + 날짜가 있는 글 → 신청 마감 임박
결과: data/econ/YYYY-MM-DD.json (그날 올라온 글, 하루 동안 누적), data/econ/index.json
"""
import datetime as dt
import html
import os
import re
import time

import trends as T
import hub as H

DIR = T.DATA / "econ"
PROBE = T.DATA / "econ_probe.json"
KST = dt.timezone(dt.timedelta(hours=9))
KOREA = [("정부 정책", "https://www.korea.kr/news/policyNewsList.do"),
         ("정부 정책", "https://www.korea.kr/briefing/pressReleaseList.do"),
         ("사실은 이렇습니다", "https://www.korea.kr/briefing/actuallyList.do")]
TOPICS = [("정부 정책", ["정부 정책 발표", "정책브리핑"]),
          ("지원금·복지", ["지원금 신청", "바우처 신청", "복지 지원 신청 접수"]),
          ("부동산", ["부동산 정책", "아파트 매매 전세"]),
          ("금리·물가", ["기준금리", "물가 상승", "대출 금리"]),
          ("주식·환율", ["코스피", "환율"]),
          ("세금·연금", ["연말정산", "국민연금", "세금 개편"]),
          ("교통·개발", ["광역철도", "GTX", "교통 호재", "신도시 개발", "철도 노선"]),
          ("경제 이슈", ["경제 이슈", "산업 이슈", "기업 이슈"])]
APPLY = re.compile(r"신청|접수|모집|지원금|바우처|공모")
DATE = re.compile(r"(\d{1,2})월\s*(\d{1,2})일\s*(?:\([월화수목금토일]\)\s*)?까지|~\s*(\d{1,2})월\s*(\d{1,2})일|~\s*(\d{1,2})\.\s*(\d{1,2})\b|(\d{1,2})\.\s*(\d{1,2})\.?\s*\([월화수목금토일]\)\s*까지")


def norm(t):
    return re.sub(r"[^가-힣a-zA-Z0-9]", "", t)[:26]


def deadline(text):
    if not APPLY.search(text):
        return None
    best = None
    for m in DATE.finditer(text):
        g = [x for x in m.groups() if x]
        mo, d = int(g[0]), int(g[1])
        if not (1 <= mo <= 12 and 1 <= d <= 31):
            continue
        y = T.TODAY.year + (1 if mo < T.TODAY.month - 6 else 0)
        try:
            e = dt.date(y, mo, d)
        except ValueError:
            continue
        if T.TODAY <= e <= T.TODAY + dt.timedelta(days=120):
            best = max(best, e) if best else e
    return str(best) if best else None


def korea_list(url):
    r = T.S.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://www.korea.kr/"})
    r.raise_for_status()
    raw = r.text
    view = re.search(r"/(\w+)List\.do", url)[1] + "View.do"        # 그 목록의 글만 (사이드바·인기글 제외)
    out, seen = [], set()
    for m in re.finditer(r'<a[^>]+href="([^"]*?newsId=(\d+)[^"]*)"[^>]*>(.*?)</a>', raw, re.S):
        href, nid, inner = m.groups()
        if nid in seen or view not in href:
            continue
        block = raw[m.start():m.end() + 600]
        title = T.txt((re.search(r'<strong[^>]*>(.*?)</strong>', inner, re.S) or [None, ""])[1]) or T.txt(inner)
        title = re.sub(r"\s+", " ", title).strip()
        title = re.sub(r"\s*(?:순위동일|NEW|▲\d+|▼\d+)$", "", title)
        if len(title) < 8:
            continue
        dm = re.search(r"(20\d\d)\.(\d\d)\.(\d\d)", block)
        tm = re.search(r"\b(\d\d):(\d\d)\b", block)
        if not dm:
            continue
        t = dt.datetime(int(dm[1]), int(dm[2]), int(dm[3]), int(tm[1]) if tm else 9, int(tm[2]) if tm else 0, tzinfo=KST)
        if t > T.NOW:
            t = T.NOW
        dept = T.txt((re.search(r'class="(?:source|dept|ministry)[^"]*"[^>]*>(.*?)<', block, re.S) or [None, ""])[1])
        link = html.unescape(href if href.startswith("http") else "https://www.korea.kr" + (href if href.startswith("/") else "/" + href))
        seen.add(nid)
        out.append({"title": title, "link": link, "t": t.isoformat(), "by": dept or "정책브리핑"})
    return out


def news(query, n=30):
    _, path = H.ROUTES["news"]
    r = T.S.get(H.BASE + path, params={"query": query, "display": n, "sort": "date"}, headers=H.headers(), timeout=20)
    r.raise_for_status()
    out = []
    for i in r.json().get("items", []):
        try:
            t = dt.datetime.strptime(i["pubDate"], "%a, %d %b %Y %H:%M:%S %z")
        except Exception:
            continue
        src = (re.search(r"//([^/]+)", i.get("originallink") or "") or [None, ""])[1]
        out.append({"title": T.txt(i.get("title")), "desc": T.txt(i.get("description")),
                    "link": i.get("originallink") or i.get("link"), "t": t.isoformat(),
                    "by": re.sub(r"^(?:www|m|news)\.", "", src)})
    return out


def save_issues(d, name, pick=None):
    """최근 이틀 기사로 '지금 이슈' 계산 → <d>/issues.json"""
    import issues
    cut = (T.NOW - dt.timedelta(hours=48)).isoformat()
    arts = []
    for p in sorted(d.glob("????-??-??.json"), reverse=True)[:3]:
        arts += [a for a in T.load(p, {"articles": []})["articles"] if a["t"] >= cut]
    out = {}
    for key, flt in (pick or {"all": None}).items():
        sub = [a for a in arts if not flt or flt(a)]
        out[key] = issues.find(sorted(sub, key=lambda a: a["t"], reverse=True)[:500])
    T.save(d / "issues.json", {"updated": T.NOW.isoformat(), **out})


def collect():
    DIR.mkdir(parents=True, exist_ok=True)
    got, probe = [], {"checked": T.NOW.isoformat()}
    korea_ok = 0
    for tag, url in KOREA:
        try:
            items = korea_list(url)
            probe[url] = len(items)
            for a in items:
                a["tags"] = [tag]
                a["gov"] = True
            got += items
            korea_ok += len(items)
        except Exception as e:
            probe[url] = str(e)[:160]
        time.sleep(0.3)
    T.save(PROBE, probe)
    if os.getenv("NAVER_HUB_ID"):
        H.probe()
        for tag, qs in TOPICS:
            if tag == "정부 정책" and korea_ok >= 10:
                continue
            for q in qs:
                try:
                    for a in news(q):
                        a["tags"] = [tag]
                        got.append(a)
                except Exception as e:
                    print(f"  (경제 뉴스 실패 {q}: {e})")
                time.sleep(0.1)
    if not got:
        print("[건너뜀] 경제: 모은 글 없음")
        return

    # 날짜별 파일에 누적 (같은 글은 하나로, 태그는 합침)
    by_day = {}
    for a in got:
        a["end"] = deadline(a["title"] + " " + a.get("desc", ""))
        a.pop("desc", None)
        day = a["t"][:10]
        if day < str(T.TODAY - dt.timedelta(days=3)):
            continue
        by_day.setdefault(day, []).append(a)
    for day, items in by_day.items():
        f = DIR / f"{day}.json"
        book = {norm(x["title"]): x for x in T.load(f, {"articles": []})["articles"]}
        for a in items:
            k = norm(a["title"])
            if k in book:
                old = book[k]
                old["tags"] = sorted(set(old["tags"]) | set(a["tags"]))
                old["end"] = old.get("end") or a["end"]
                old["gov"] = old.get("gov") or a.get("gov", False)
            else:
                book[k] = a
        rows = sorted(book.values(), key=lambda x: x["t"], reverse=True)[:400]
        T.save(f, {"date": day, "updated": T.NOW.isoformat(), "articles": rows})
    files = sorted(DIR.glob("????-??-??.json"), reverse=True)
    for old in files[10:]:
        old.unlink()
    T.save(DIR / "index.json", [{"date": p.stem, "count": len(T.load(p, {"articles": []})["articles"])} for p in files[:10]])
    save_issues(DIR, "econ")
    print(f"[OK] 경제: 정책브리핑 {korea_ok}건, 전체 {len(got)}건 수집")
