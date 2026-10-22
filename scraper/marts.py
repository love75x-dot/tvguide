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
    if not force and prev and T.NOW.hour not in RUN_HOURS:
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
    hol_key = json.dumps(hol, ensure_ascii=False) if hol else None
    hol_changed = hol and seen.get("traders_holiday") != hol_key
    if hol:
        seen["traders_holiday"] = hol_key
    T.save(SEEN, seen)

    keep = lambda new_v, key, sub: new_v if new_v is not None else (prev.get(key) or {}).get(sub, [])
    data = {
        "updated": T.NOW.isoformat(), "slot": f"{T.TODAY} {T.NOW.hour}",
        "costco": {"sale": keep(sale, "costco", "sale"), "new": keep(new, "costco", "new"),
                   "blog": blogs.get("costco") or []},
        "traders": {"holiday": hol or (prev.get("traders") or {}).get("holiday"),
                    "news": tnews or [], "blog": blogs.get("traders") or []},
        "daiso": {"exhibits": keep(exb, "daiso", "exhibits"), "blog": blogs.get("daiso") or []},
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
    if hol_changed and hol:
        lines.append(f"\n📅 <b>{html.escape(hol['title'] or '트레이더스 휴점일')}</b>")
        for r in hol["rows"]:
            lines.append(f"• {html.escape(r['dates'])}: {html.escape(r['stores'])}")
    if lines:
        site = os.getenv("SITE_URL")
        telegram("🏬 <b>마트·다이소 새 소식</b>\n\n" + "\n".join(lines) + (f"\n\n🔗 {site}/#shop" if site else ""))
        print(f"  텔레그램 알림 보냄 ({len(lines)}줄)")
