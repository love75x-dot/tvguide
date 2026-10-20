// 텔레그램 편성표 봇 (Cloudflare Worker)
// 환경변수(Worker → Settings → Variables and Secrets):
//   BOT_TOKEN       텔레그램 봇 토큰 (Secret)
//   SITE_URL        편성표 사이트 주소 예) https://내프로젝트.pages.dev
//   WEBHOOK_SECRET  아무 영문/숫자 문자열 (Secret, 위조 요청 차단용)
//   GH_TOKEN        GitHub 토큰 (Secret, 매시간 수집 실행용)
//   GH_REPO         예) love75x-dot/tvguide (Text)
// Settings → Trigger events 에 Cron "7 * * * *" 를 추가하면 매시간 GitHub 수집을 실행합니다.

const HELP = `📺 편성표 봇 사용법

/지금 — 지금 방송 중인 프로그램
/다음 — 곧 시작할 프로그램
/오늘 — 오늘 저녁 황금시간대 편성
/편성 KBS2 — 채널별 오늘 편성
/채널 — 채널 목록
/뉴스 — 최신 연예 이슈 5개

(영문 명령어도 됩니다: /now /next /today /ch KBS2 /news)`;

const WD = ["일", "월", "화", "수", "목", "금", "토"];
const kst = (d) => new Date(new Date(d).getTime() + 9 * 3600e3);
const hm = (d) => kst(d).toISOString().slice(11, 16);
const day = (d) => kst(d).toISOString().slice(0, 10);
const esc = (s) => String(s).replace(/[&<>]/g, (m) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[m]));
const norm = (s) => s.toLowerCase().replace(/[\s_-]/g, "");

async function getJSON(env, path) {
  const r = await fetch(`${env.SITE_URL.replace(/\/$/, "")}/${path}?t=${Date.now()}`, {
    cf: { cacheTtl: 60 },
  });
  if (!r.ok) throw new Error(`${path} ${r.status}`);
  return r.json();
}

function nowPlaying(data, now) {
  const rows = data.channels.map((c) => {
    const p = c.programs.find((p) => new Date(p.start) <= now && now < new Date(p.end));
    if (!p) return `<b>${esc(c.name)}</b> — 정보 없음`;
    const left = Math.round((new Date(p.end) - now) / 6e4);
    return `<b>${esc(c.name)}</b> ${esc(p.title)}\n   ${hm(p.start)}~${hm(p.end)} (${left}분 남음)`;
  });
  return `🔴 <b>지금 방송 중</b> (${hm(now)} 기준)\n\n` + rows.join("\n");
}

function upNext(data, now) {
  const rows = data.channels.map((c) => {
    const p = c.programs.find((p) => new Date(p.start) > now);
    return p ? `<b>${esc(c.name)}</b> ${hm(p.start)} ${esc(p.title)}` : `<b>${esc(c.name)}</b> — 정보 없음`;
  });
  return `⏭ <b>다음 방송</b>\n\n` + rows.join("\n");
}

function tonight(data, now) {
  const d = day(now);
  const a = new Date(`${d}T18:00:00+09:00`), b = new Date(`${d}T23:59:59+09:00`);
  const parts = data.channels.map((c) => {
    const ps = c.programs.filter((p) => new Date(p.start) >= a && new Date(p.start) <= b);
    if (!ps.length) return "";
    return `\n<b>📺 ${esc(c.name)}</b>\n` + ps.map((p) => `${hm(p.start)} ${esc(p.title)}`).join("\n");
  });
  const k = kst(now);
  return `🌙 <b>오늘 저녁 편성</b> ${k.getUTCMonth() + 1}/${k.getUTCDate()}(${WD[k.getUTCDay()]})\n` + parts.join("\n");
}

function channel(data, name, now) {
  const c = data.channels.find((c) => norm(c.name) === norm(name) || norm(c.id) === norm(name));
  if (!c) return `'${esc(name)}' 채널을 찾지 못했어요.\n/채널 로 목록을 확인해 주세요.`;
  const ps = c.programs.filter((p) => day(p.start) === day(now));
  if (!ps.length) return `${esc(c.name)} 오늘 편성 정보가 없어요.`;
  const rows = ps.map((p) => {
    const live = new Date(p.start) <= now && now < new Date(p.end);
    const past = new Date(p.end) <= now;
    const line = `${hm(p.start)} ${esc(p.title)}`;
    return live ? `▶️ <b>${line}</b>` : past ? `<s>${line}</s>` : line;
  });
  return `📺 <b>${esc(c.name)} 오늘 편성</b>${c.ok === false ? " ⚠ 최신 아님" : ""}\n\n` + rows.join("\n");
}

async function reply(env, chatId, text) {
  for (let i = 0; i < text.length; i += 4000) {
    await fetch(`https://api.telegram.org/bot${env.BOT_TOKEN}/sendMessage`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        chat_id: chatId, text: text.slice(i, i + 4000),
        parse_mode: "HTML", disable_web_page_preview: true,
      }),
    });
  }
}

async function handle(env, text) {
  const [cmdRaw, ...rest] = text.trim().split(/\s+/);
  const cmd = cmdRaw.replace(/@.*$/, "").toLowerCase();
  const arg = rest.join(" ");
  const now = new Date();
  const footer = `\n\n🔗 ${env.SITE_URL}`;

  if (["/start", "/help", "/도움말"].includes(cmd)) return HELP;
  if (["/뉴스", "/news"].includes(cmd)) {
    const n = await getJSON(env, "data/news.json");
    return "🔥 <b>최신 연예 이슈</b>\n\n" +
      n.articles.slice(0, 5).map((a) => `• <a href="${esc(a.link)}">${esc(a.title)}</a>`).join("\n");
  }
  const data = await getJSON(env, "data/schedule.json");
  if (["/지금", "/now"].includes(cmd)) return nowPlaying(data, now) + footer;
  if (["/다음", "/next"].includes(cmd)) return upNext(data, now) + footer;
  if (["/오늘", "/today"].includes(cmd)) return tonight(data, now) + footer;
  if (["/채널", "/channels"].includes(cmd))
    return "📡 채널 목록\n\n" + data.channels.map((c) => `• ${esc(c.name)}`).join("\n");
  if (["/편성", "/ch"].includes(cmd))
    return arg ? channel(data, arg, now) : "채널명을 함께 보내 주세요. 예) /편성 KBS2";
  // 명령어 없이 채널명만 보내도 동작
  if (data.channels.some((c) => norm(c.name) === norm(text))) return channel(data, text, now);
  return HELP;
}

async function runCollector(env) {
  const r = await fetch(
    `https://api.github.com/repos/${env.GH_REPO}/actions/workflows/update.yml/dispatches`, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${env.GH_TOKEN}`,
        Accept: "application/vnd.github+json",
        "User-Agent": "tvguide-bot",
      },
      body: JSON.stringify({ ref: "main" }),
    });
  if (r.status !== 204) throw new Error(`GitHub 실행 실패 ${r.status}: ${await r.text()}`);
}

export default {
  // 매시간 예약 실행 (Cron Trigger)
  async scheduled(event, env, ctx) {
    ctx.waitUntil(runCollector(env));
  },

  async fetch(request, env) {
    // 수동 확인용: 브라우저에서 /run?key=WEBHOOK_SECRET 열면 즉시 수집 실행
    const u = new URL(request.url);
    if (request.method === "GET" && u.pathname === "/run") {
      if (u.searchParams.get("key") !== env.WEBHOOK_SECRET) return new Response("forbidden", { status: 403 });
      try { await runCollector(env); return new Response("수집 실행 요청 완료"); }
      catch (e) { return new Response(String(e), { status: 500 }); }
    }
    if (request.method !== "POST") return new Response("편성표 봇 작동 중");
    if (env.WEBHOOK_SECRET &&
        request.headers.get("X-Telegram-Bot-Api-Secret-Token") !== env.WEBHOOK_SECRET)
      return new Response("forbidden", { status: 403 });

    const update = await request.json();
    const msg = update.message || update.edited_message;
    if (msg?.text) {
      let out;
      try { out = await handle(env, msg.text); }
      catch (e) { out = "편성표를 불러오지 못했어요. 잠시 후 다시 시도해 주세요."; }
      await reply(env, msg.chat.id, out);
    }
    return new Response("ok");
  },
};

export { handle };
