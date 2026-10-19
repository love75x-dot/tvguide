"""
방송사 공식 편성표 수집기
- 채널별 '어댑터' 함수가 각 방송사 사이트에서 편성표를 가져옵니다.
- 한 채널이 실패해도 다른 채널은 계속 수집하고, 실패한 채널은 직전 데이터를 유지합니다.
- 응답 원본은 data/raw/ 에 저장됩니다(형식이 바뀌었을 때 원인 확인용).
"""
import datetime as dt
import html
import json
import pathlib
import re
import traceback
import xml.etree.ElementTree as ET

import requests

KST = dt.timezone(dt.timedelta(hours=9))
ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "schedule.json"
RAW = ROOT / "data" / "raw"
DAYS = 7  # 오늘부터 며칠치
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
}


# ---------- 공통 도구 ----------
def get_json(url, name, extra_headers=None, **kw):
    r = requests.get(url, headers={**HEADERS, **(extra_headers or {})}, timeout=20, **kw)
    r.raise_for_status()
    RAW.mkdir(parents=True, exist_ok=True)
    (RAW / f"{name}.json").write_text(r.text, encoding="utf-8")
    return r.json()


def pick(d, *keys):
    for k in keys:
        if isinstance(d, dict) and d.get(k) not in (None, ""):
            return d[k]
    return None


def to_dt(day: dt.date, hhmm: str):
    """'0530', '05:30', '053000', '25:10'(다음날 01:10) 등을 KST 시각으로."""
    s = str(hhmm).replace(":", "").strip()
    h, m = int(s[:2]), int(s[2:4])
    return dt.datetime(day.year, day.month, day.day, tzinfo=KST) + dt.timedelta(hours=h, minutes=m)


def finalize(programs):
    """시작순 정렬, 빈 종료시각은 다음 프로그램 시작으로 채움, 중복 제거."""
    programs = sorted({p["start"]: p for p in programs if p.get("title")}.values(),
                      key=lambda p: p["start"])
    for i, p in enumerate(programs):
        if not p.get("end") and i + 1 < len(programs):
            p["end"] = programs[i + 1]["start"]
    return [p for p in programs if p.get("end")]


def prog(start, end, title, genre=None):
    return {"start": start.isoformat(), "end": end.isoformat() if end else None,
            "title": str(title).strip(), "genre": genre}


def days():
    today = dt.datetime.now(KST).date()
    return [today + dt.timedelta(days=i) for i in range(DAYS)]


# ---------- 채널 어댑터 ----------
def kbs(channel_code):
    """KBS 편성 API. channel_code: 11=KBS1, 12=KBS2"""
    d = days()
    url = ("https://static.api.kbs.co.kr/mediafactory/v1/schedule/weekly"
           f"?rtype=json&local_station_code=00&channel_code={channel_code}"
           f"&program_planned_date_from={d[0]:%Y%m%d}&program_planned_date_to={d[-1]:%Y%m%d}")
    data = get_json(url, f"kbs_{channel_code}")
    out = []
    for ch in data if isinstance(data, list) else [data]:
        for s in pick(ch, "schedules") or []:
            day = dt.datetime.strptime(s["service_date"], "%Y%m%d").date()
            st = to_dt(day, s["program_planned_start_time"])
            en = to_dt(day, s["program_planned_end_time"]) if s.get("program_planned_end_time") else None
            if en and en <= st:
                en += dt.timedelta(days=1)
            out.append(prog(st, en, pick(s, "program_title", "programming_table_title"),
                            pick(s, "program_genre")))
    return out


def per_day(name, fn):
    """오늘 편성은 필수, 이후 날짜는 아직 미공개일 수 있으니 실패해도 건너뜀."""
    out = []
    for i, day in enumerate(days()):
        try:
            out += fn(day)
        except Exception as e:
            if i == 0:
                raise
            print(f"  ({name} {day} 건너뜀: {e})")
    return out


def mbc():
    """MBC 공식 편성 API. BroadType 0 = 결방(스포츠 중계 등으로 대체)이라 제외."""
    def one(day):
        res = []
        url = f"https://control.imbc.com/Schedule/TV?sDate={day:%Y%m%d}&sType=ALL"
        data = get_json(url, f"mbc_{day:%Y%m%d}",
                        extra_headers={"Referer": "https://schedule.imbc.com/"})
        for s in data:
            if str(s.get("BroadType", "1")).strip() == "0":
                continue
            st = to_dt(day, s["StartTime"])
            en = to_dt(day, s["EndTime"]) if s.get("EndTime") else None
            if en and en <= st:
                en += dt.timedelta(days=1)
            res.append(prog(st, en, s["Title"]))
        return res
    return per_day('mbc', one)


def sbs():
    out = []
    for day in days():
        url = f"https://static.cloud.sbs.co.kr/schedule/{day.year}/{day.month}/{day.day}/SBS.json"
        data = get_json(url, f"sbs_{day:%Y%m%d}")
        for s in data if isinstance(data, list) else pick(data, "list", "data") or []:
            st = to_dt(day, pick(s, "start_time", "starttime"))
            en_raw = pick(s, "end_time", "endtime")
            en = to_dt(day, en_raw) if en_raw else None
            if en and en <= st:
                en += dt.timedelta(days=1)
            out.append(prog(st, en, pick(s, "title", "program_title"), pick(s, "genre")))
    return out


# ---------- HTML 편성표 공통 ----------
def get_html(url, name, referer):
    r = requests.get(url, timeout=20, headers={**HEADERS, "Accept": "text/html,*/*",
                                               "Referer": referer})
    r.raise_for_status()
    if r.encoding in (None, "ISO-8859-1"):
        r.encoding = r.apparent_encoding
    RAW.mkdir(parents=True, exist_ok=True)
    (RAW / f"{name}.html").write_text(r.text, encoding="utf-8")
    return r.text


def txt(s):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s))).strip()


def day_progs(day, items):
    """[(시작 'HH:MM', 종료 'HH:MM' 또는 None, 제목)] → 프로그램 목록.
    시각이 거꾸로 가면(자정 넘김) 다음날로 처리."""
    out, prev = [], None
    for st_s, en_s, title in items:
        st = to_dt(day, st_s)
        if prev and st < prev - dt.timedelta(hours=3):
            st += dt.timedelta(days=1)
        prev = st
        en = None
        if en_s:
            en = to_dt(st.date(), en_s)
            if en <= st:
                en += dt.timedelta(days=1)
        out.append(prog(st, en, title))
    if len(out) < 5:
        raise ValueError(f"{day} 편성 항목 {len(out)}개 (페이지 구조 변경 가능성)")
    return out


def jtbc():
    def one(day):
        res = []
        raw = get_html(f"https://jtbc.co.kr/schedule/jtbc/{day:%Y%m%d}", f"jtbc_{day:%Y%m%d}",
                       "https://jtbc.co.kr/schedule")
        items = re.findall(r'class="time_v">\s*(\d{1,2}:\d{2}).*?<strong class="title">(.*?)</strong>',
                           raw, re.S)
        res += day_progs(day, [(t, None, txt(n)) for t, n in items])
        return res
    return per_day('jtbc', one)


def tvchosun():
    def one(day):
        res = []
        raw = get_html(f"https://broadcast.tvchosun.com/onair/schedule/today.cstv?date={day:%Y%m%d}&ch=",
                       f"tvchosun_{day:%Y%m%d}", "https://broadcast.tvchosun.com/")
        items = re.findall(r'id="stime" value="(\d{4})".*?id="etime" value="(\d{4})".*?'
                           r'<a class="title"[^>]*>(.*?)</a>', raw, re.S)
        res += day_progs(day, [(s, e, txt(n)) for s, e, n in items])
        return res
    return per_day('tvchosun', one)


def channela():
    def one(day):
        res = []
        raw = get_html(f"https://ichannela.com/com/cmm/schedule.do?selectedDate={day:%Y%m%d}",
                       f"channela_{day:%Y%m%d}", "https://ichannela.com/")
        items = re.findall(r'<div class="time">\s*<strong>(\d{1,2}:\d{2})</strong>.*?'
                           r'<strong class="title">(.*?)</strong>', raw, re.S)
        res += day_progs(day, [(t, None, re.sub(r"^\[[^\]]*\]\s*", "", txt(n))) for t, n in items])
        return res
    return per_day('channela', one)


def mbn():
    def one(day):
        res = []
        url = f"https://www.mbn.co.kr/xml/scheduleXML.mbn?date={day:%Y/%m/%d}"
        raw = get_html(url, f"mbn_{day:%Y%m%d}", "https://www.mbn.co.kr/vod/schedule")
        root = ET.fromstring(raw.encode("utf-8"))
        for p in root.iter("program"):
            d = dt.date.fromisoformat(p.findtext("air_date").strip())
            st = to_dt(d, p.findtext("start_time"))
            en_raw = (p.findtext("end_time") or "").strip()
            en = to_dt(d, en_raw) if re.fullmatch(r"\d{1,2}:\d{2}", en_raw) else None
            if en and en <= st:      # 종료시각이 비어 있으면 다음 프로그램 시작으로 채움
                en += dt.timedelta(days=1)
            title = (p.findtext("program_title") or "").strip()
            ep = (p.findtext("bcast_episode") or "").strip()
            if ep and ep != "0":
                title += f" {ep}회"
            res.append(prog(st, en, title, (p.findtext("attribute") or "").strip() or None))
        return res
    return per_day('mbn', one)


def tvn():
    """tvN 공식 편성표 페이지에 들어 있는 주간 편성 데이터(시작·종료 시각 포함)."""
    raw = get_html("https://tvn.cjenm.com/ko/tvn-schedule/", "tvn", "https://tvn.cjenm.com/ko/")
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', raw, re.S)
    if not m:
        raise ValueError("tvN 편성 데이터 블록을 찾지 못함")
    found = []

    def walk(o):
        if isinstance(o, dict):
            if isinstance(o.get("schePgmList"), list):
                found.extend(o["schePgmList"])
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(json.loads(m.group(1)))
    out = []
    for p in found:
        if p.get("chnNm") not in (None, "tvN"):
            continue
        st = dt.datetime.fromtimestamp(p["bdStrDtm"], KST)
        en = dt.datetime.fromtimestamp(p["bdEndDtm"], KST) if p.get("bdEndDtm") else None
        title = re.sub(r"^\[[^\]]*\]\s*", "", (p.get("pgmNm") or "").strip())
        ep = re.search(r"(\d+)\s*[회화]", p.get("pgmEpinoNm") or "")
        if ep:
            title += f" {ep.group(1)}회"
        out.append(prog(st, en, title))
    return out


def ena():
    """ENA 공식 편성표. 방송 길이(분)로 종료 시각 계산."""
    def one(day):
        raw = get_html(f"https://ktena.co.kr/schedule/?d={day:%Y%m%d}", f"ena_{day:%Y%m%d}",
                       "https://ktena.co.kr/schedule/")
        rows = re.findall(r'<div class="col time">\s*(\d{1,2}:\d{2})\s*</div>\s*'
                          r'<div class="col tit">\s*<strong>(.*?)</strong>.*?'
                          r'<div class="col summ">\s*(\d*)\s*</div>', raw, re.S)
        res = day_progs(day, [(t, None, txt(n)) for t, n, _ in rows])
        for p, (_, _, mins) in zip(res, rows):
            if mins:
                p["end"] = (dt.datetime.fromisoformat(p["start"])
                            + dt.timedelta(minutes=int(mins))).isoformat()
        return res
    return per_day("ena", one)


# 채널 추가는 여기에 한 줄씩: (id, 표시이름, 수집함수)
CHANNELS = [
    ("kbs1", "KBS1", lambda: kbs(11)),
    ("kbs2", "KBS2", lambda: kbs(12)),
    ("mbc", "MBC", mbc),
    ("sbs", "SBS", sbs),
    ("jtbc", "JTBC", jtbc),
    ("tvchosun", "TV조선", tvchosun),
    ("channela", "채널A", channela),
    ("mbn", "MBN", mbn),
    ("tvn", "tvN", tvn),
    ("ena", "ENA", ena),
]


# ---------- 실행 ----------
def main():
    prev = {}
    if OUT.exists():
        prev = {c["id"]: c for c in json.loads(OUT.read_text(encoding="utf-8"))["channels"]}

    now = dt.datetime.now(KST).isoformat()
    channels, failed = [], []
    for cid, name, fn in CHANNELS:
        try:
            programs = finalize(fn())
            if not programs:
                raise ValueError("편성 데이터가 비어 있음 (사이트 형식 변경 가능성)")
            channels.append({"id": cid, "name": name, "ok": True, "updated": now,
                             "programs": programs})
            print(f"[OK] {name}: {len(programs)}개")
        except Exception as e:
            failed.append(name)
            print(f"[실패] {name}: {e}")
            traceback.print_exc()
            old = prev.get(cid, {"programs": [], "updated": None})
            channels.append({"id": cid, "name": name, "ok": False, "error": str(e),
                             "updated": old.get("updated"), "programs": old["programs"]})

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"generated": now, "channels": channels},
                              ensure_ascii=False, indent=1), encoding="utf-8")
    if failed:
        print("::warning::수집 실패 채널: " + ", ".join(failed))


if __name__ == "__main__":
    main()
