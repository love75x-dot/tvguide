"""
신차 탭 수집 (매시간)
  1) 네이버 뉴스: 신차·사전계약·가격 공개·리콜·판매 순위·중고차·오토바이
  2) 화제의 차: 최근 3일 기사에 자주 나온 차 + 네이버 검색 추이
  3) 출시 달력: 출시·사전계약 기사에서 날짜 뽑기
  4) 텔레그램: 사전계약·가격 공개·출시·리콜 (하루 최대 6건)
결과: data/cars/YYYY-MM-DD.json(그날 기사), data/cars/summary.json(화제·달력)
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

DIR = T.DATA / "cars"
SEEN = T.DATA / "cars_seen.json"
CONF = T.ROOT / "config" / "cars.json"
TOPICS = [("신차", ["신차 출시", "신차 사전계약", "출시 예정 신차", "신형 공개 자동차"]),
          ("현대", ["현대차 신차", "현대자동차 출시"]), ("기아", ["기아 신차", "기아 출시"]),
          ("제네시스", ["제네시스 신차"]), ("KGM·르노·GM", ["KGM 신차", "르노코리아 신차", "쉐보레 신차"]),
          ("수입차", ["수입차 신차 출시", "BMW 신형", "벤츠 신형", "테슬라 국내 출시"]),
          ("전기차", ["전기차 신차", "전기차 보조금"]), ("오토바이", ["오토바이 신모델", "바이크 신차 출시", "스쿠터 출시", "오토바이", "이륜차", "바이크 업계", "배달 오토바이", "할리데이비슨", "혼다 바이크"]),
          ("업계 이슈", ["자동차 업계", "완성차 업계 이슈", "전기차 화재", "자율주행", "자동차 관세"]),
          ("리콜·결함", ["자동차 리콜", "차량 결함"]), ("판매·순위", ["자동차 판매 순위", "신차 판매량"]),
          ("중고차·시세", ["중고차 시세"])]
NOT_CAR = re.compile(r"주가|증시|목표주가|배당|채용|노조|파업|임단협|부동산|분양")
FLAGS = [("사전계약", r"사전\s?계약"), ("가격 공개", r"가격\s?(?:공개|확정|책정)|판매\s?가격|가격은|\d천만\s?원"),
         ("출시", r"출시"), ("리콜", r"리콜|결함|무상\s?수리"), ("판매 1위", r"판매\s?1위|1위"),
         ("가격 인하", r"가격\s?인하|할인"), ("연식변경", r"연식\s?변경"), ("보조금", r"보조금"), ("중고차 시세", r"중고")]
DATE = re.compile(r"(\d{1,2})월\s*(\d{1,2})일")
PART = r"(?![A-Za-z])"


def models():
    out = []
    for name, kind in T.load(CONF, {"models": []})["models"]:
        word, _, need = name.partition("|")
        pat = re.compile(r"(?<![A-Za-z0-9가-힣])" + re.escape(word).replace(r"\ ", r"\s?") + PART)
        out.append({"name": word, "kind": kind, "pat": pat, "need": need})
    return out


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
        out.append({"title": T.txt(i.get("title")), "desc": T.txt(i.get("description")),
                    "link": i.get("originallink") or i.get("link"), "t": t.isoformat(),
                    "by": re.sub(r"^(?:www|m|news)\.", "", src)})
    return out


def norm(t):
    return re.sub(r"[^가-힣a-zA-Z0-9]", "", t)[:26]


def collect():
    if not os.getenv("NAVER_HUB_ID"):
        return
    H.probe()
    DIR.mkdir(parents=True, exist_ok=True)
    MS = models()
    got = {}
    for tag, qs in TOPICS:
        for q in qs:
            try:
                items = news(q)
            except Exception as e:
                print(f"  (신차 검색 실패 {q}: {e})")
                continue
            for a in items:
                if NOT_CAR.search(a["title"]):
                    continue
                k = norm(a["title"])
                if k in got:
                    if tag not in got[k]["tags"]:
                        got[k]["tags"].append(tag)
                    continue
                a["tags"] = [tag]
                got[k] = a
            time.sleep(0.1)
    for a in got.values():
        text = a["title"]
        a["cars"] = [m["name"] for m in MS if m["pat"].search(text) and (not m["need"] or m["need"] in text)]
        a["flags"] = [f for f, p in FLAGS if re.search(p, a["title"])]
        a["moto"] = bool(re.search(r"오토바이|바이크|이륜차|스쿠터|모터사이클|라이더|\d{2,4}\s?cc|할리데이비슨|모토라드|야마하", a["title"])) \
            or any(m["kind"] == "moto" and m["name"] in a["cars"] for m in MS)
        if a["moto"] and "오토바이" not in a["tags"]:
            a["tags"].append("오토바이")
        if not a["moto"] and "오토바이" in a["tags"]:
            a["tags"].remove("오토바이")

    got = {k: a for k, a in got.items() if a["tags"]}        # 오토바이 검색에서 왔지만 오토바이 기사가 아닌 것 버림
    # 날짜별 파일에 누적
    cut3 = str(T.TODAY - dt.timedelta(days=3))
    by_day = {}
    for a in got.values():
        if a["t"][:10] >= cut3:
            by_day.setdefault(a["t"][:10], []).append(a)
    for day, items in by_day.items():
        f = DIR / f"{day}.json"
        book = {norm(x["title"]): x for x in T.load(f, {"articles": []})["articles"]}
        for a in items:
            b = {k: v for k, v in a.items() if k != "desc"}
            if norm(a["title"]) in book:
                o = book[norm(a["title"])]
                o["tags"] = sorted(set(o["tags"]) | set(b["tags"]))
            else:
                book[norm(a["title"])] = b
        T.save(f, {"date": day, "updated": T.NOW.isoformat(),
                   "articles": sorted(book.values(), key=lambda x: x["t"], reverse=True)[:400]})
    files = sorted(DIR.glob("????-??-??.json"), reverse=True)
    for old in files[10:]:
        old.unlink()
    T.save(DIR / "index.json", [{"date": p.stem, "count": len(T.load(p, {"articles": []})["articles"])} for p in files[:10]])

    # 최근 6일치 기사 (화제·달력 계산용)
    arts = []
    for p in files[:7]:
        arts += T.load(p, {"articles": []})["articles"]
    seen_t = set()
    arts = [a for a in arts if not (norm(a["title"]) in seen_t or seen_t.add(norm(a["title"])))]
    recent = [a for a in arts if a["t"] >= (T.NOW - dt.timedelta(days=3)).isoformat()]
    before = [a for a in arts if a not in recent]

    # 화제의 차
    cnt, why, prev = {}, {}, {}
    for a in recent:
        for c in a.get("cars", []):
            cnt[c] = cnt.get(c, 0) + 1
            for f in a.get("flags", []):
                why.setdefault(c, {})[f] = why.setdefault(c, {}).get(f, 0) + 1
    for a in before:
        for c in a.get("cars", []):
            prev[c] = prev.get(c, 0) + 1
    kinds = {m["name"]: m["kind"] for m in MS}
    hot = sorted(cnt, key=lambda c: -cnt[c])[:10]
    rise = {}
    try:
        end = T.TODAY - dt.timedelta(days=1)
        start = end - dt.timedelta(days=13)
        for i in range(0, len(hot), 5):
            for k, s in H.trend(hot[i:i + 5], start, end).items():
                a3, b7 = sum(s[-3:]) / 3, sum(s[-10:-3]) / 7
                rise[k] = round((a3 - b7) / b7 * 100) if b7 > 0.5 else None
            time.sleep(0.15)
    except Exception as e:
        print(f"  (차 검색 추이 실패: {e})")
    hot_rows = [{"name": c, "count": cnt[c], "prev": prev.get(c, 0), "rise": rise.get(c), "moto": kinds.get(c) == "moto",
                 "why": [f for f, _ in sorted(why.get(c, {}).items(), key=lambda x: -x[1])[:3]]} for c in hot]

    # 출시 달력
    cal = {}
    for a in sorted(arts, key=lambda x: x["t"]):
        if not re.search(r"출시|사전\s?계약|공개", a["title"]) or not a.get("cars"):
            continue
        status, day = None, None
        for m in DATE.finditer(a["title"]):
            mo, d = int(m[1]), int(m[2])
            y = T.TODAY.year + (1 if mo < T.TODAY.month - 6 else 0)
            try:
                dd = dt.date(y, mo, d)
            except ValueError:
                continue
            if dd >= T.TODAY:
                status, day = "soon", str(dd)
        if not status:
            if re.search(r"출시\s?예정|연내|내년|출시\s?앞|출격\s?예고|공개\s?예정", a["title"]):
                status = "tbd"
            elif re.search(r"사전\s?계약", a["title"]):
                status, day = "presale", a["t"][:10]
            elif re.search(r"출시|출격", a["title"]):
                status, day = "out", a["t"][:10]
        if not status:
            continue
        for c in a["cars"][:1]:
            old = cal.get(c)
            rank = {"soon": 3, "presale": 2, "out": 1, "tbd": 0}
            if not old or rank[status] >= rank[old["status"]]:
                cal[c] = {"name": c, "status": status, "date": day, "title": a["title"], "link": a["link"], "moto": kinds.get(c) == "moto"}
    cal_rows = sorted(cal.values(), key=lambda x: ({"soon": 0, "presale": 1, "out": 2, "tbd": 3}[x["status"]], x["date"] or "9"))[:12]
    import econ
    econ.save_issues(DIR, "cars", {"car": lambda a: not a.get("moto"), "moto": lambda a: a.get("moto")})
    T.save(DIR / "summary.json", {"updated": T.NOW.isoformat(), "hot": hot_rows, "calendar": cal_rows})

    # 텔레그램 (사전계약·가격 공개·출시·리콜, 하루 최대 6건)
    seen = T.load(SEEN, None)
    first = seen is None
    seen = seen or {"keys": [], "sent": {}}
    today_sent = seen["sent"].get(str(T.TODAY), 0)
    cands = [a for a in got.values() if norm(a["title"]) not in seen["keys"] and a.get("cars")
             and set(a["flags"]) & {"사전계약", "가격 공개", "출시", "리콜"} and a["t"] >= (T.NOW - dt.timedelta(hours=6)).isoformat()]
    send = []
    used = set()
    for a in sorted(cands, key=lambda x: x["t"], reverse=True):
        if a["cars"][0] in used:
            continue
        used.add(a["cars"][0])
        send.append(a)
    send = send[:max(0, 6 - today_sent)]
    if send and not first:
        lines = [f"🚗 <b>신차·자동차 소식 {len(send)}건</b>"]
        for a in send:
            lines.append(f"\n• [{html.escape(a['cars'][0])} · {html.escape('/'.join(f for f in a['flags'] if f in ('사전계약','가격 공개','출시','리콜')))}] "
                         f"<a href=\"{html.escape(a['link'])}\">{html.escape(a['title'])}</a>")
        try:
            M.telegram("".join(lines))
            seen["sent"][str(T.TODAY)] = today_sent + len(send)
        except Exception as e:
            print(f"::warning::신차 알림 실패: {e}")
    seen["keys"] = (seen["keys"] + [norm(a["title"]) for a in got.values() if norm(a["title"]) not in seen["keys"]])[-4000:]
    seen["sent"] = {k: v for k, v in seen["sent"].items() if k >= str(T.TODAY - dt.timedelta(days=3))}
    T.save(SEEN, seen)
    print(f"[OK] 신차: 기사 {len(got)}건, 화제 {len(hot_rows)}대, 달력 {len(cal_rows)}개, 알림 {len(send) if not first else 0}건")
