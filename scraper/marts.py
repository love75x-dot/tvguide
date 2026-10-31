"""
마트·다이소 소식 수집 + 텔레그램 알림
  - 코스트코: 스페셜 할인 상품, 신상품 (공식 온라인몰 데이터)
  - 다이소: 신상·기획전 목록, 오픈 예정 기획전 (다이소몰 데이터)
  - 트레이더스: 점포별 휴점일 (공식), 행사 소식 (네이버 뉴스·블로그)
  - 세 곳 모두 네이버 블로그 최신 후기
새로 올라온 할인·신상·기획전은 텔레그램으로 알림.
실행: 오전 8시, 낮 12시, 저녁 6시대 (또는 자료가 없을 때)
"""
import datetime as dt
import html
import json
import os
import re
import time

import trends as T
import hub as H

OUT = T.DATA / "marts.json"
SEEN = T.DATA / "marts_seen.json"
RUN_HOURS = {8, 12, 18}
COSTCO = "https://www.costco.co.kr"
DAISO = "https://www.daisomall.co.kr"
COSTCO_CAT = {"Foods": "식품", "FurnitureBeddingHome": "가구·침구·홈", "HomeKitchen": "주방", "ClothingBagsAccessories": "의류·잡화",
              "BeautyHouseholdPersonal-Care": "뷰티·생활", "Appliances": "가전", "HealthSupplement": "건강",
              "BabyKidsToysPets": "유아·완구·반려", "HardwareAutomotive": "공구·자동차", "PatioLawnGarden": "정원·야외",
              "ElectronicsComputers": "전자·컴퓨터", "Gift-Set-Special": "선물세트", "SportsFitnessCamping": "스포츠·캠핑",
              "JewelryWatchesAccessories": "주얼리·시계", "StationeryOffice-Supplies": "문구·사무"}
BULK = re.compile(r"x\s*\d{3,}\s*(개|팩|박스|ea|EA)")      # 사업자용 대량 상품 (x 100개 이상)
UA = {"User-Agent": T.UA, "Accept": "application/json, text/plain, */*", "Accept-Language": "ko-KR,ko;q=0.9"}


def kst_date(s):
    try:
        return dt.datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(T.KST).date().isoformat()
    except Exception:
        return None


# ---------------- 코스트코 ----------------
def costco_list(category, max_items=1200):
    items, page = [], 0
    while True:
        r = T.S.get(f"{COSTCO}/rest/v2/korea/products/search", timeout=25, headers={**UA, "Referer": COSTCO + "/"},
                    params={"fields": "FULL", "query": "", "category": category, "pageSize": 100,
                            "currentPage": page, "lang": "ko", "curr": "KRW"})
        r.raise_for_status()
        j = r.json()
        for p in j.get("products", []):
            if BULK.search(p.get("name") or ""):
                continue
            cd = p.get("couponDiscount") or {}
            disc = (p.get("discountPrice") or {}).get("value") or cd.get("discountValue") or 0
            img = ((p.get("images") or [{}])[0]).get("url") or ""
            items.append({
                "id": p.get("code"), "name": p.get("name"), "url": COSTCO + (p.get("url") or ""),
                "cat": COSTCO_CAT.get((p.get("url") or "/").split("/")[1], "기타"),
                "price": (p.get("price") or {}).get("value"), "base": (p.get("basePrice") or {}).get("value"),
                "discount": disc, "start": kst_date(cd.get("discountStartDate") or p.get("discountStartDate") or ""),
                "end": kst_date(cd.get("discountEndDate") or p.get("discountEndDate") or ""),
                "img": (COSTCO + img) if img.startswith("/") else img,
                "rating": p.get("averageRating"), "reviews": p.get("numberOfReviews"),
                "soldout": (p.get("stock") or {}).get("stockLevelStatus") == "outOfStock"})
        pg = j.get("pagination") or {}
        page += 1
        if page >= (pg.get("totalPages") or 0) or len(items) >= max_items:
            break
        time.sleep(0.3)
    return items


# ---------------- 다이소 ----------------
def daiso_exhibits():
    out = {}
    for path in ("/ds/exbt/selPrirExbt", "/ds/exbt/selExbt"):
        r = T.S.post("https://fapi.daisomall.co.kr" + path, data="{}", timeout=25,
                     headers={**UA, "Content-Type": "application/json", "Origin": DAISO, "Referer": DAISO + "/"})
        r.raise_for_status()
        for x in r.json().get("data") or []:
            no = x.get("exbtNo")
            if not no or no in out:
                continue
            st, ed = (x.get("exhStDtm") or "")[:10], (x.get("exhEdDtm") or "")[:10]
            out[no] = {"id": no, "name": html.unescape(x.get("exbtNm") or ""), "theme": x.get("exbtThmeNm") or "",
                       "start": st, "end": "" if ed.startswith("9999") else ed,
                       "url": f"{DAISO}/ds/spexhbt/C211?exbtNo={no}",
                       "upcoming": st > str(T.TODAY), "new": "신상" in (x.get("exbtNm") or "")}
    items = [v for v in out.values() if v["name"] and (not v["end"] or v["end"] >= str(T.TODAY))]
    items.sort(key=lambda v: v["start"], reverse=True)
    return items[:60]


# ---------------- 트레이더스 ----------------
def traders_holidays():
    r = T.S.get("https://api-eapp.emart.com/eapp-api/api/v1/etccode/TRADERS_HP_HOLIDAY", timeout=20,
                headers={**UA, "Origin": "https://www.traders.co.kr", "Referer": "https://www.traders.co.kr/"})
    r.raise_for_status()
    raw = html.unescape(r.json()["data"]["code_desc"])
    title = T.txt((re.search(r"<h1>(.*?)</h1>", raw, re.S) or [None, ""])[1])
    rows = [{"dates": T.txt(a), "stores": T.txt(b)}
            for a, b in re.findall(r"<dt>(.*?)</dt>\s*<dd>(.*?)</dd>", raw, re.S)]
    return {"title": title, "rows": rows}


# ---------------- 네이버 뉴스·블로그 ----------------
def naver_recent(kind, query, n=12):
    if kind not in H.ROUTES:
        return []
    _, path = H.ROUTES[kind]
    r = T.S.get(H.BASE + path, params={"query": query, "display": n, "sort": "date"}, headers=H.headers(), timeout=20)
    r.raise_for_status()
    out = []
    for i in r.json().get("items", []):
        d = i.get("postdate") or ""
        if not d and i.get("pubDate"):
            try:
                d = dt.datetime.strptime(i["pubDate"][:25], "%a, %d %b %Y %H:%M:%S").strftime("%Y%m%d")
            except Exception:
                d = ""
        out.append({"title": T.txt(i.get("title")), "link": i.get("originallink") or i.get("link"),
                    "date": f"{d[:4]}-{d[4:6]}-{d[6:8]}" if len(d) == 8 else "",
                    "by": T.txt(i.get("bloggername") or "")})
    return out


# ---------------- 할인 행사 기사 ----------------
DEAL_EVENTS = [("넾다세일", "넾다세일", ["넾다세일", "넾다 세일"]),
               ("블프", "블랙프라이데이 할인", ["블랙프라이데이", "블프"]),
               ("쓱데이", "쓱데이", ["쓱데이"]),
               ("코세페", "코리아세일페스타", ["코리아세일페스타", "코세페"]),
               ("십일절", "십일절 할인", ["십일절"]),
               ("빅스마일", "빅스마일데이", ["빅스마일"])]
DEAL_TOPICS = [("대형마트", ["이마트 할인 행사", "홈플러스 할인 행사", "롯데마트 할인 행사"]),
               ("편의점", ["편의점 할인 행사"]),
               ("온라인몰", ["쿠팡 할인 행사", "온라인 쇼핑 할인 행사"])]
DEAL_END = re.compile(r"(?:~|∼|부터\s*)?(\d{1,2})월\s*(\d{1,2})일\s*까지|~\s*(\d{1,2})[./](\d{1,2})")


def news_items(query, n=30):
    _, path = H.ROUTES["news"]
    r = T.S.get(H.BASE + path, params={"query": query, "display": n, "sort": "date"}, headers=H.headers(), timeout=20)
    r.raise_for_status()
    out = []
    for i in r.json().get("items", []):
        try:
            t = dt.datetime.strptime(i["pubDate"], "%a, %d %b %Y %H:%M:%S %z")
        except Exception:
            continue
        out.append({"title": T.txt(i.get("title")), "desc": T.txt(i.get("description")),
                    "link": i.get("originallink") or i.get("link"), "t": t.isoformat(),
                    "by": re.sub(r"^(?:www|m|news)\.", "", (re.search(r"//([^/]+)", i.get("originallink") or "") or [None, ""])[1])})
    return out


def deals():
    cut = (T.NOW - dt.timedelta(days=3)).isoformat()
    arts, events = {}, []
    def add(items, tag):
        for a in items:
            if a["t"] < cut:
                continue
            k = re.sub(r"[^가-힣a-zA-Z0-9]", "", a["title"])[:24]
            if k in arts:
                if tag not in arts[k]["tags"]:
                    arts[k]["tags"].append(tag)
                continue
            a["tags"] = [tag]
            arts[k] = a
    for name, q, words in DEAL_EVENTS:
        items = [a for a in (safe(news_items, q)[0] or []) if any(w in a["title"] + a["desc"] for w in words)]
        add(items, name)
        recent = [a for a in items if a["t"] >= cut]
        if len(recent) >= 2:
            ends = {}
            for a in recent:
                for m in DEAL_END.finditer(a["title"] + " " + a["desc"]):
                    mo, d = int(m.group(1) or m.group(3)), int(m.group(2) or m.group(4))
                    if 1 <= mo <= 12 and 1 <= d <= 31:
                        y = T.TODAY.year + (1 if mo < T.TODAY.month - 6 else 0)
                        try:
                            e = dt.date(y, mo, d)
                        except ValueError:
                            continue
                        if e >= T.TODAY - dt.timedelta(days=1):
                            ends[str(e)] = ends.get(str(e), 0) + 1
            end = max(ends, key=lambda k: (ends[k], k)) if ends else None
            events.append({"name": name, "count": len(recent), "end": end, "latest": max(a["t"] for a in recent)})
        time.sleep(0.1)
    for name, qs in DEAL_TOPICS:
        for q in qs:
            add([a for a in (safe(news_items, q, 20)[0] or []) if re.search(r"할인|세일|특가|행사|반값|1\+1", a["title"])], name)
            time.sleep(0.1)
    rows = sorted(arts.values(), key=lambda a: a["t"], reverse=True)[:150]
    for a in rows:
        a.pop("desc", None)
    events.sort(key=lambda e: (e["end"] is None, e["end"] or "", -e["count"]))
    return {"events": events, "articles": rows}


def safe(fn, *a, **k):
    try:
        return fn(*a, **k), None
    except Exception as e:
        print(f"  [실패] {fn.__name__}: {e}")
        return None, str(e)[:200]


# ---------------- 텔레그램 ----------------
def telegram(text):
    token, chat = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    if not (token and chat):
        return
    for i in range(0, len(text), 3900):
        T.S.post(f"https://api.telegram.org/bot{token}/sendMessage", timeout=20,
                 json={"chat_id": chat, "text": text[i:i + 3900], "parse_mode": "HTML",
                       "disable_web_page_preview": True})


def won(v):
    return f"{int(v):,}원" if v else ""


def collect(force=False):
    prev = T.load(OUT, {})
    if not force and prev and "items" in (prev.get("daiso") or {}) and T.NOW.hour not in RUN_HOURS:
        return
    if not force and prev.get("slot") == f"{T.TODAY} {T.NOW.hour}":
        return
    if os.getenv("NAVER_HUB_ID"):
        H.probe()
    errors = {}
    sale, errors["costco_sale"] = safe(costco_list, "SpecialPriceOffers")
    new, errors["costco_new"] = safe(costco_list, "whatsnew", 300)
    exb, errors["daiso"] = safe(daiso_exhibits)
    hol, errors["traders"] = safe(traders_holidays)
    blogs = {}
    for key, q in [("costco", "코스트코 추천템"), ("traders", "트레이더스 추천템"), ("daiso", "다이소 신상")]:
        blogs[key], _ = safe(naver_recent, "blog", q)
    tnews, _ = safe(naver_recent, "news", "트레이더스 할인 행사")
    # 다이소 핫템·인기템·품절템·신상 (뉴스 + 블로그)
    dgroups = {}
    for name, q in [("핫템", "다이소 핫템"), ("인기템", "다이소 인기템"), ("품절템", "다이소 품절템"), ("신상", "다이소 신상")]:
        rows = []
        for kind in ("news", "blog"):
            r, _ = safe(naver_recent, kind, q, 15)
            for x in r or []:
                x["kind"] = kind
                rows.append(x)
        rows.sort(key=lambda x: x.get("date") or "", reverse=True)
        dgroups[name] = rows[:20]
    # 트레이더스·코스트코 추천템 (블로그) — 회원이 아니어도 '후기 글'로 쓰기 좋은 상품
    tpick, _ = safe(naver_recent, "blog", "트레이더스 추천템 이번주", 15)
    cpick, _ = safe(naver_recent, "blog", "코스트코 추천템 이번주", 15)
    import issues
    def items_of(rows):
        arts = [{"title": x["title"], "link": x["link"], "t": x.get("date") or "", "by": x.get("by") or x["link"][:40]} for x in rows]
        return [{"name": i["name"], "count": i["count"], "link": i["items"][0]["link"]} for i in issues.find(arts, top=10, min_src=2)]
    dhot = items_of([x for v in dgroups.values() for x in v])
    deal, errors["deals"] = safe(deals) if os.getenv("NAVER_HUB_ID") else (None, None)

    # 처음 본 날짜 기록 → NEW 표시와 알림
    seen = T.load(SEEN, {})
    first_run = not seen
    fresh = {}
    for key, items in [("costco_sale", sale), ("costco_new", new), ("daiso", exb)]:
        if items is None:                       # 실패하면 직전 자료 유지
            continue
        book = seen.setdefault(key, {})
        fresh[key] = [x for x in items if x["id"] not in book]
        for x in items:
            book.setdefault(x["id"], str(T.TODAY))
            x["first"] = book[x["id"]]
        # 오래된 기록 정리(60일)
        cut = str(T.TODAY - dt.timedelta(days=60))
        seen[key] = {k: v for k, v in book.items() if v >= cut or k in {x["id"] for x in items}}
    new_events = []
    if deal:
        ev_seen = seen.setdefault("deal_events", {})
        for e in deal["events"]:
            if e["name"] not in ev_seen or ev_seen[e["name"]] < str(T.TODAY - dt.timedelta(days=20)):
                new_events.append(e)
            ev_seen[e["name"]] = str(T.TODAY)
    hol_key = json.dumps(hol, ensure_ascii=False) if hol else None
    hol_changed = hol and seen.get("traders_holiday") != hol_key
    if hol:
        seen["traders_holiday"] = hol_key
    T.save(SEEN, seen)

    keep = lambda new_v, key, sub: new_v if new_v is not None else (prev.get(key) or {}).get(sub, [])
    data = {
        "updated": T.NOW.isoformat(), "slot": f"{T.TODAY} {T.NOW.hour}",
        "costco": {"sale": keep(sale, "costco", "sale"), "new": keep(new, "costco", "new"),
                   "blog": blogs.get("costco") or [], "pick": cpick or [],
                   "items": items_of((cpick or []) + (blogs.get("costco") or []))},
        "traders": {"holiday": hol or (prev.get("traders") or {}).get("holiday"),
                    "news": tnews or [], "blog": blogs.get("traders") or [], "pick": tpick or [],
                    "items": items_of((tnews or []) + (tpick or []) + (blogs.get("traders") or []))},
        "daiso": {"exhibits": keep(exb, "daiso", "exhibits"), "blog": blogs.get("daiso") or [], "groups": dgroups, "items": dhot},
        "deals": deal or prev.get("deals") or {"events": [], "articles": []},
        "errors": {k: v for k, v in errors.items() if v},
    }
    T.save(OUT, data)
    print(f"[OK] 마트: 코스트코 할인 {len(data['costco']['sale'])} · 신상 {len(data['costco']['new'])} · "
          f"다이소 기획전 {len(data['daiso']['exhibits'])} · 트레이더스 휴점 {len((hol or {}).get('rows', []))}")

    if first_run:
        print("  (첫 실행: 기준만 저장, 알림 없음)")
        return
    lines = []
    s = sorted(fresh.get("costco_sale", []), key=lambda x: -(x["discount"] or 0))
    if s:
        lines.append(f"🏷 <b>코스트코 새 할인 {len(s)}개</b>")
        for x in s[:10]:
            lines.append(f"• <a href=\"{x['url']}\">{html.escape(x['name'])}</a> {won(x['price'])}"
                         + (f" (−{won(x['discount'])})" if x["discount"] else "") + (f" ~{x['end'][5:]}" if x["end"] else ""))
        if len(s) > 10:
            lines.append(f"  …외 {len(s) - 10}개")
    n = fresh.get("costco_new", [])
    if n:
        lines.append(f"\n🆕 <b>코스트코 신상품 {len(n)}개</b>")
        for x in n[:8]:
            lines.append(f"• <a href=\"{x['url']}\">{html.escape(x['name'])}</a> {won(x['price'])}")
    d = fresh.get("daiso", [])
    if d:
        lines.append(f"\n🛍 <b>다이소 새 기획전 {len(d)}개</b>")
        for x in d[:8]:
            lines.append(f"• <a href=\"{x['url']}\">{html.escape(x['name'])}</a>"
                         + (f" (오픈 {x['start'][5:]})" if x["upcoming"] else ""))
    if new_events:
        lines.append(f"\n🔥 <b>새 할인 행사 소식</b>")
        for e in new_events:
            top = next((a for a in data["deals"]["articles"] if e["name"] in a["tags"]), None)
            lines.append(f"• <b>{html.escape(e['name'])}</b>" + (f" (~{e['end'][5:]})" if e["end"] else "")
                         + (f" — <a href=\"{top['link']}\">{html.escape(top['title'])}</a>" if top else ""))
    if hol_changed and hol:
        lines.append(f"\n📅 <b>{html.escape(hol['title'] or '트레이더스 휴점일')}</b>")
        for r in hol["rows"]:
            lines.append(f"• {html.escape(r['dates'])}: {html.escape(r['stores'])}")
    if lines:
        site = os.getenv("SITE_URL")
        telegram("🏬 <b>마트·다이소 새 소식</b>\n\n" + "\n".join(lines) + (f"\n\n🔗 {site}/#shop" if site else ""))
        print(f"  텔레그램 알림 보냄 ({len(lines)}줄)")
