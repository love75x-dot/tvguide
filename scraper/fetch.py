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
def get_json(url, name, **kw):
    r = requests.get(url, headers=HEADERS, timeout=20, **kw)
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


def mbc():
    out = []
    for day in days():
        url = f"https://control.imbc.com/Schedule/PCONT?sDate={day:%Y%m%d}&sType=TV"
        data = get_json(url, f"mbc_{day:%Y%m%d}")
        for s in data if isinstance(data, list) else pick(data, "list", "data") or []:
            if pick(s, "Channel", "ChannelCode") not in (None, "M", "MBC"):
                continue
            st = to_dt(day, pick(s, "StartTime", "starttime"))
            out.append(prog(st, None, pick(s, "ProgramTitle", "Title", "title"),
                            pick(s, "TypeTitle", "Genre")))
    return out


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


# ---------- 범용 HTML 편성표 수집기 ----------
TIME_RE = re.compile(r"^(?:[01]?\d|2[0-9]):[0-5]\d$")
INLINE_RE = re.compile(r"^((?:[01]?\d|2[0-9]):[0-5]\d)\s*[~\-]?\s*(?:(?:[01]?\d|2[0-9]):[0-5]\d)?\s+(.{2,})$")
SKIP = {"본방", "재방", "생방", "HD", "UHD", "자막", "수화", "해설", "다시보기", "VOD", "편성표",
        "LIVE", "ON AIR", "방송중", "15", "12", "19", "7", "ALL", "전체"}


def html_text_tokens(raw):
    raw = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", raw)
    raw = re.sub(r"(?s)<[^>]+>", "\n", raw)
    toks = [html.unescape(t).strip() for t in raw.split("\n")]
    return [re.sub(r"\s+", " ", t) for t in toks if t.strip()]


def parse_html_schedule(raw, day):
    """페이지에서 'HH:MM → 프로그램명' 순서를 찾아냅니다."""
    toks, out, i = html_text_tokens(raw), [], 0
    while i < len(toks):
        t = toks[i]
        m = INLINE_RE.match(t)
        if m and m.group(2) not in SKIP:
            out.append((m.group(1), m.group(2)))
        elif TIME_RE.match(t):
            for j in range(i + 1, min(i + 6, len(toks))):
                c = toks[j]
                if TIME_RE.match(c) or re.match(r"^[~\-]\s*\d", c):
                    continue
                if c not in SKIP and len(c) >= 2 and not c.isdigit():
                    out.append((t, c))
                    i = j
                    break
        i += 1
    progs, prev = [], None
    for hhmm, title in out:
        st = to_dt(day, hhmm.zfill(5))
        if prev and st < prev - dt.timedelta(hours=6):   # 자정 넘어가면 다음날
            st += dt.timedelta(days=1)
        prev = st
        progs.append(prog(st, None, title))
    if len(progs) < 5:
        raise ValueError(f"편성 항목을 {len(progs)}개밖에 찾지 못함 (페이지 구조 확인 필요)")
    return progs


def html_channel(name, url_tpl, date_fmt="%Y%m%d", days_n=DAYS):
    """url_tpl 에 {date} 가 있으면 날짜별로, 없으면 오늘 하루만 수집."""
    def run():
        out, ds = [], days() if "{date}" in url_tpl else days()[:1]
        for day in ds[:days_n]:
            url = url_tpl.format(date=day.strftime(date_fmt))
            r = requests.get(url, headers={**HEADERS, "Accept": "text/html,*/*"}, timeout=20)
            r.raise_for_status()
            r.encoding = r.apparent_encoding if r.encoding in (None, "ISO-8859-1") else r.encoding
            RAW.mkdir(parents=True, exist_ok=True)
            (RAW / f"{name}_{day:%Y%m%d}.html").write_text(r.text, encoding="utf-8")
            out += parse_html_schedule(r.text, day)
        return out
    return run


# 종편 4사 편성표 주소 — 첫 실행 결과에 따라 주소/날짜형식을 조정합니다.
JTBC = html_channel("jtbc", "https://tv.jtbc.co.kr/schedule?date={date}")
TVCHOSUN = html_channel("tvchosun", "https://broadcast.tvchosun.com/broadcast/schedule.cstv?date={date}", "%Y-%m-%d")
CHANNELA = html_channel("channela", "https://www.ichannela.com/schedule/schedule.do?date={date}")
MBN = html_channel("mbn", "https://www.mbn.co.kr/vod/schedule?date={date}")


# 채널 추가는 여기에 한 줄씩: (id, 표시이름, 수집함수)
CHANNELS = [
    ("kbs1", "KBS1", lambda: kbs(11)),
    ("kbs2", "KBS2", lambda: kbs(12)),
    ("mbc", "MBC", mbc),
    ("sbs", "SBS", sbs),
    ("jtbc", "JTBC", JTBC),
    ("tvchosun", "TV조선", TVCHOSUN),
    ("channela", "채널A", CHANNELA),
    ("mbn", "MBN", MBN),
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
