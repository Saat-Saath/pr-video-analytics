import fs from "node:fs/promises";
import path from "node:path";

// Instagram views via Apify's Instagram Scraper (Bright Data does not return IG play counts).
// Usage: node apify_instagram.mjs --urls-file urls.json --output outputs/apify/result.json
const ACTOR = "apify~instagram-scraper";

async function loadDotEnv() {
  try {
    const text = await fs.readFile(path.resolve(".env"), "utf8");
    for (const line of text.split(/\r?\n/)) {
      const m = line.match(/^\s*([A-Z0-9_]+)\s*=\s*(.*)\s*$/);
      if (m && !process.env[m[1]]) process.env[m[1]] = m[2];
    }
  } catch {}
}

function arg(name) {
  const i = process.argv.indexOf(`--${name}`);
  return i > -1 ? process.argv[i + 1] : undefined;
}

const shortcode = (u) => (String(u).match(/\/(?:p|reel|reels|tv)\/([^/?#]+)/i) || [])[1] || null;

async function api(token, url, options = {}) {
  const res = await fetch(url, {
    ...options,
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json", ...(options.headers ?? {}) },
  });
  const text = await res.text();
  if (!res.ok) throw new Error(`Apify HTTP ${res.status}: ${text.slice(0, 500)}`);
  return JSON.parse(text);
}

async function main() {
  await loadDotEnv();
  const token = process.env.APIFY_TOKEN;
  if (!token) throw new Error("APIFY_TOKEN missing (add it to .env)");
  const urlsFile = arg("urls-file");
  const urls = arg("url") ? [arg("url")] : JSON.parse(await fs.readFile(path.resolve(urlsFile), "utf8")).map((x) => (typeof x === "string" ? x : x.url));
  const output = arg("output") ?? path.join("outputs", "apify", `instagram-${Date.now()}.json`);

  // Start run, then poll (avoids long-held HTTP connections).
  // --run-id lets a long run be collected later without starting a new (paid) run.
  const run = arg("run-id")
    ? (await api(token, `https://api.apify.com/v2/actor-runs/${arg("run-id")}`)).data
    : (await api(token, `https://api.apify.com/v2/acts/${ACTOR}/runs`, {
        method: "POST",
        body: JSON.stringify({ directUrls: urls, resultsType: "posts", resultsLimit: 1, addParentData: false }),
      })).data;
  if (process.argv.includes("--start-only")) { process.stdout.write(JSON.stringify({ run_id: run.id, status: run.status }) + "\n"); return; }
  let status = run.status;
  // With --run-id (collecting a run started earlier) check once and report RUNNING instead of waiting.
  const polls = arg("run-id") ? 0 : 120;
  for (let i = 0; i < polls && !["SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT"].includes(status); i += 1) {
    await new Promise((r) => setTimeout(r, 5000));
    status = (await api(token, `https://api.apify.com/v2/actor-runs/${run.id}`)).data.status;
  }
  if (["READY", "RUNNING"].includes(status)) throw new Error(`RUNNING: Apify run ${run.id} is still ${status}`);
  if (status !== "SUCCEEDED") throw new Error(`Apify run ${run.id} ended with ${status}`);
  const items = await api(token, `https://api.apify.com/v2/datasets/${run.defaultDatasetId}/items?clean=true&format=json`);

  const byCode = new Map(items.map((it) => [shortcode(it.url) || it.shortCode, it]));
  const fetchedAt = new Date().toISOString();
  const records = urls.map((u) => {
    const it = byCode.get(shortcode(u));
    if (!it || it.error) return { platform: "instagram", url: u, fetch_status: "Not returned", error: it?.error ?? null, fetched_at: fetchedAt };
    return {
      platform: "instagram",
      url: u,
      title: it.caption ?? null,
      published_at: it.timestamp ?? null,
      views: it.videoPlayCount ?? it.videoViewCount ?? null,
      likes: it.likesCount ?? null,
      comments: it.commentsCount ?? null,
      shares: null,
      saves: null,
      account: it.ownerUsername ?? null,
      fetch_status: "OK",
      source: "Apify",
      run_id: run.id,
      fetched_at: fetchedAt,
      raw: it,
    };
  });

  await fs.mkdir(path.dirname(path.resolve(output)), { recursive: true });
  await fs.writeFile(path.resolve(output), JSON.stringify({ status: "ready", platform: "instagram", source: "Apify", run_id: run.id, record_count: records.length, records }, null, 2) + "\n");
  process.stdout.write(JSON.stringify({ status: "ready", run_id: run.id, record_count: records.length, output }) + "\n");
}

main().catch((e) => { process.stderr.write(`${e.message}\n`); process.exitCode = 1; });
