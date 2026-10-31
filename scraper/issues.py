"""
기사 제목에서 '지금 이슈'를 뽑기
  최근 기사 제목에 여러 번 나온 단어·두 단어 묶음(예: 경기남부광역철도, 전기차 화재)을
  여러 언론사가 다룬 순으로 고르고, 이슈마다 대표 기사를 붙임
"""
import re

STOP = set("""기자 단독 속보 종합 오늘 내일 어제 올해 내년 지난 이번 최대 최고 최초 공개 발표 진행 개최 확대 지원 시작 예정 관련
위해 대해 통해 이후 이상 이하 가능 제공 운영 선정 추진 강화 대상 결과 가운데 국내 세계 한국 서울 전국 지역 업계 기업 회사
사업 행사 신규 이벤트 혜택 사진 영상 포토 인터뷰 화제 인기 논란 대표 전망 분석 증가 감소 기록 돌파 달성 상승 하락 마련 확인
정부 시장 경제 뉴스 이슈 소식 정책 출시 신차 판매 차량 자동차 건강 효과 방법 주의 필요 가격 할인 만원 억원 조원 천억 미국 중국 일본
대통령 관리 치료 질환 환자 병원 기아 현대 삼성 애플 갤럭시 아이폰 테슬라 서비스 기술 제품 모델 브랜드 고객 소비자 시민
영업익 영업이익 매출 실적 순이익 분기 맞아 맞이
이유 문제 우려 계획 검토 도입 시행 변경 확정 신청 접수 모집 상황 정도 모든 가장 다시 함께 위한 있는 없는 하는 했다 한다 된다""".split())
JOSA = re.compile(r"(으로|에서|에게|까지|부터|처럼|보다|이라|이며|이고|하고|하는|했다|한다|된다|으로서|에는|에도|은|는|이|가|을|를|의|에|와|과|도|로|만)$")
WORD = re.compile(r"[가-힣A-Za-z0-9]{2,}")


def words(title):
    t = re.sub(r"\[[^\]]*\]|\([^)]*\)|<[^>]*>", " ", title)
    out = []
    for w in WORD.findall(t):
        w2 = JOSA.sub("", w) if len(w) > 2 else w
        if len(w2) >= 2 and w2 not in STOP and not re.fullmatch(r"\d+(?:월|일|년|분기|개|명|곳|%)?|[A-Za-z]{1,2}", w2):
            out.append(w2)
    return out


def find(articles, top=6, min_src=3):
    """articles: [{title, link, t, by}] → [{name, count, items:[...]}]"""
    df, where = {}, {}
    for i, a in enumerate(articles):
        ws = words(a["title"])
        grams = set(ws) | {ws[j] + " " + ws[j + 1] for j in range(len(ws) - 1)}
        for g in grams:
            df[g] = df.get(g, 0) + 1
            where.setdefault(g, []).append(i)
    # 여러 언론사가 다룬 것만
    cand = []
    for g, n in df.items():
        if n < min_src:
            continue
        one = " " not in g
        if one and len(g) < 4 and n < 12:       # 짧은 한 단어(대통령·관리 등)는 아주 많이 나올 때만
            continue
        srcs = {articles[i].get("by") for i in where[g]}
        if len(srcs) < min_src:
            continue
        score = len(srcs) * (1.6 if " " in g else 1.0) * (1.2 if len(g.replace(" ", "")) >= 4 else 1.0)
        cand.append((score, g))
    cand.sort(reverse=True)
    picked, used = [], set()
    for score, g in cand:
        ids = set(where[g])
        if any(g in p["name"] or p["name"] in g for p in picked):
            continue
        if used and len(ids & used) / len(ids) > 0.6:      # 이미 고른 이슈와 거의 같은 기사들
            continue
        items = sorted((articles[i] for i in ids), key=lambda a: a["t"], reverse=True)
        picked.append({"name": g, "count": len({a.get("by") for a in items}), "items": [
            {"title": a["title"], "link": a["link"], "t": a["t"], "by": a.get("by", "")} for a in items[:5]]})
        used |= ids
        if len(picked) >= top:
            break
    return picked
