// Cloudflare Worker: reliable cron that dispatches GitHub Actions workflows (GitHub's own schedule runs hours late on this account).
// Secrets: GH_TOKEN (GitHub token with workflow/actions:write scope on kushagraagarwal-11/mbg-datav2-cron).
const REPO = "kushagraagarwal-11/mbg-datav2-cron";
const JOBS = {
  "7,37 * * * *": ["csp-not-reached-today.yml"],   // every 30 min: Today - CSP tried, cx not reached (Google Sheet tab)
  "15 */2 * * *": ["six-live-refresh.yml"],         // every 2 h: seven-live-CSPs page -> csp-atoms-live.pages.dev
};

async function dispatch(file, env) {
  const r = await fetch(`https://api.github.com/repos/${REPO}/actions/workflows/${file}/dispatches`, {
    method: "POST",
    headers: { "Authorization": `Bearer ${env.GH_TOKEN}`, "Accept": "application/vnd.github+json", "User-Agent": "wiom-cron-dispatcher", "X-GitHub-Api-Version": "2022-11-28" },
    body: JSON.stringify({ ref: "main" }),
  });
  const txt = r.status === 204 ? "dispatched" : await r.text();
  console.log(file, r.status, txt.slice(0, 200));
  return r.status === 204;
}

export default {
  async scheduled(event, env, ctx) {
    const files = JOBS[event.cron] || [];
    for (const f of files) ctx.waitUntil(dispatch(f, env));
  },
  async fetch(req, env) {
    const u = new URL(req.url);
    if (u.pathname === "/run" && u.searchParams.get("key") && u.searchParams.get("key") === env.RUN_KEY) {   // optional manual trigger
      const f = u.searchParams.get("wf") || "csp-not-reached-today.yml";
      return new Response((await dispatch(f, env)) ? `dispatched ${f}` : `failed ${f}`, { status: 200 });
    }
    return new Response("wiom cron dispatcher: " + Object.entries(JOBS).map(([c, f]) => `${c} -> ${f.join(",")}`).join(" | "), { status: 200 });
  },
};
