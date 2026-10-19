# 방송 편성표

각 방송사 공식 편성표를 1시간마다 자동으로 수집해 한 페이지에서 보여줍니다.

## 설치 (GitHub)
1. GitHub에서 새 저장소(public)를 만들고 이 폴더의 파일을 모두 올립니다.
   (`.github` 폴더도 꼭 함께 올려야 합니다.)
2. **Actions** 탭 → "편성표 업데이트" → **Run workflow** 로 첫 수집을 실행합니다.

## Cloudflare Pages로 연결
1. Cloudflare 대시보드 → **Workers & Pages → Create → Pages → Connect to Git**
2. 위 GitHub 저장소 선택
3. 빌드 설정: Framework `None`, Build command 비움, Output directory `/`
4. **Save and Deploy** → `https://<프로젝트명>.pages.dev` 주소가 생깁니다.
5. 내 도메인을 쓰려면 프로젝트 → **Custom domains**에서 추가합니다. (예: tv.내도메인.com)

GitHub Actions가 1시간마다 편성표를 커밋하면 Cloudflare가 자동으로 다시 배포합니다.

## 연예 이슈 텔레그램 알림
1. 텔레그램에서 **@BotFather** → `/newbot` → 봇 토큰을 받습니다.
2. 만든 봇에게 아무 메시지나 보낸 뒤, 브라우저에서
   `https://api.telegram.org/bot<토큰>/getUpdates` 를 열어 `"chat":{"id":숫자` 의 숫자를 확인합니다.
3. GitHub 저장소 **Settings → Secrets and variables → Actions → New repository secret**:
   - `TELEGRAM_BOT_TOKEN` = 봇 토큰
   - `TELEGRAM_CHAT_ID` = 위 숫자
4. (선택) 네이버 뉴스도 받으려면 developers.naver.com에서 '검색' API를 신청하고
   `NAVER_CLIENT_ID`, `NAVER_CLIENT_SECRET` 를 같은 방식으로 등록합니다.
5. 첫 실행은 기준만 잡고 알림을 보내지 않습니다. 두 번째 실행부터 새 기사만 알려줍니다.

수집 키워드는 `scraper/news.py` 맨 위 `KEYWORDS` 에서 바꿀 수 있습니다.

## 텔레그램으로 편성표 보기

### ① 자동 발송 (매일 08시·19시)
- 위 텔레그램 설정만 끝나면 자동으로 동작합니다.
- 메시지에 사이트 링크를 넣으려면 GitHub **Settings → Secrets and variables → Actions → Variables** 탭에
  `SITE_URL` = `https://<프로젝트명>.pages.dev` 를 등록합니다.
- 발송 시각과 시간대는 `scraper/digest.py` 맨 위 `SCHEDULE` 에서 바꿉니다.
- 테스트: Actions → Run workflow → 입력칸에 `19` → 바로 발송됩니다.

### ② 명령어 봇 (Cloudflare Workers)
1. Cloudflare → **Workers & Pages → Create → Worker** → 이름 `tvguide-bot` → Deploy
2. **Edit code** → `worker/worker.js` 내용을 전부 붙여넣고 Deploy
3. Worker → **Settings → Variables and Secrets** 에 추가:
   - `BOT_TOKEN` (Secret) = 텔레그램 봇 토큰
   - `SITE_URL` (Text) = `https://<프로젝트명>.pages.dev`
   - `WEBHOOK_SECRET` (Secret) = 아무 영문·숫자 문자열 (예: tv2026secret)
4. 텔레그램에 Worker 주소를 연결합니다. 브라우저 주소창에 아래를 한 번 열면 됩니다.
   ```
   https://api.telegram.org/bot<봇토큰>/setWebhook?url=https://tvguide-bot.<계정>.workers.dev&secret_token=<WEBHOOK_SECRET값>
   ```
   `"ok":true` 가 나오면 완료입니다.

⚠ **순서 주의:** 웹훅을 연결하면 `getUpdates` 로 chat id를 볼 수 없게 됩니다.
chat id를 먼저 확인하고 GitHub Secret에 등록한 뒤에 4번을 진행하세요.

**사용 가능한 명령어**
| 명령어 | 내용 |
|---|---|
| `/지금` | 지금 방송 중인 프로그램 + 남은 시간 |
| `/다음` | 채널별 다음 프로그램 |
| `/오늘` | 오늘 저녁 18시~24시 편성 |
| `/편성 KBS2` 또는 `KBS2` | 그 채널 오늘 전체 편성 (방송 중 ▶️ 표시) |
| `/채널` | 채널 목록 |
| `/뉴스` | 최신 연예 이슈 5개 |

## 내 웹사이트에 넣기
```html
<iframe src="https://<프로젝트명>.pages.dev/" style="width:100%;height:900px;border:0"></iframe>
```

## 수집이 실패했을 때
- Actions 실행 기록에서 `[실패] 채널명` 줄과 오류 메시지를 확인합니다.
- 실패한 채널은 직전 데이터를 그대로 보여주고, 페이지에 "⚠ 최신 정보 아님"이 표시됩니다.
- 오류 메시지와 `data/raw/` 안의 해당 채널 파일을 Claude에게 보내면 수정할 수 있습니다.

## 채널 추가
`scraper/fetch.py`의 `CHANNELS` 목록에 수집 함수를 추가합니다.
