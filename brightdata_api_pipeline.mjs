import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";

const API_BASE = "https://api.brightdata.com/datasets/v3";
const DATASETS = Object.freeze({
  // Default Instagram analytics route: the Reels scraper returns view/play metrics.
  instagram: "gd_lyclm20il4r5helnj",
  instagram_reels: "gd_lyclm20il4r5helnj",
  instagram_posts: "gd_lk5ns7kz21pck8jpis",
  facebook: "gd_lyclm1571iy3mv57zw",
  youtube: "gd_lk56epmy2i5g7lzu0k",
});

function parseArgs(argv) {
  const args = { command: argv[0] ?? "help" };
  for (let i = 1; i < argv.length; i += 1) {
    const token = argv[i];
    if (!token.startsWith("--")) throw new Error(`Unexpected argument: ${token}`);
    const key = token.slice(2).replaceAll("-", "_");
    const value = argv[i + 1];
    if (!value || value.startsWith("--")) {
      args[key] = true;
    } else {
      args[key] = value;
      i += 1;
    }
  }
  return args;
}

async function loadDotEnv() {
  try {
    const text = await fs.readFile(path.resolve(".env"), "utf8");
    for (const line of text.split(/\r?\n/)) {
      const m = line.match(/^\s*([A-Z0-9_]+)\s*=\s*(.*)\s*$/);
      if (m && !process.env[m[1]]) process.env[m[1]] = m[2];
    }
  } catch {}
}

async function loadApiKey() {
  await loadDotEnv();
  if (process.env.BRIGHTDATA_API_KEY) return process.env.BRIGHTDATA_API_KEY.trim();

  const credentialPath = path.join(
    process.env.APPDATA ?? path.join(os.homedir(), "AppData", "Roaming"),
    "brightdata-cli",
    "credentials.json",
  );
  const raw = await fs.readFile(credentialPath, "utf8");
  const parsed = JSON.parse(raw);
  if (!parsed.api_key) {
    throw new Error(`No api_key found in ${credentialPath}`);
  }
  return String(parsed.api_key).trim();
}

function safeJson(text) {
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

async function apiRequest(apiKey, url, options = {}) {
  const response = await fetch(url, {
    ...options,
    headers: {
      Authorization: `Bearer ${apiKey}`,
      ...(options.body ? { "Content-Type": "application/json" } : {}),
      ...(options.headers ?? {}),
    },
  });
  const text = await response.text();
  const body = safeJson(text);
  if (!response.ok) {
    const detail = typeof body === "string" ? body : JSON.stringify(body);
    throw new Error(`Bright Data HTTP ${response.status}: ${detail}`);
  }
  return body;
}

async function triggerCollection(apiKey, platform, urls) {
  const datasetId = DATASETS[platform];
  if (!datasetId) throw new Error(`Unsupported platform: ${platform}`);
  if (!urls.length) throw new Error("At least one URL is required");

  const query = new URLSearchParams({
    dataset_id: datasetId,
    notify: "false",
    include_errors: "true",
    format: "json",
  });
  const result = await apiRequest(apiKey, `${API_BASE}/trigger?${query}`, {
    method: "POST",
    body: JSON.stringify({ input: urls.map((url) => ({ url })) }),
  });
  if (!result?.snapshot_id) throw new Error("Bright Data did not return a snapshot_id");
  return result.snapshot_id;
}

async function waitUntilReady(apiKey, snapshotId, { attempts = 60, intervalMs = 10_000 } = {}) {
  for (let attempt = 1; attempt <= attempts; attempt += 1) {
    const progress = await apiRequest(apiKey, `${API_BASE}/progress/${encodeURIComponent(snapshotId)}`);
    if (progress?.status === "ready") return progress;
    if (["failed", "canceled"].includes(progress?.status)) {
      throw new Error(`Snapshot ${progress.status}: ${progress.error_message ?? "no details"}`);
    }
    if (attempt < attempts) await new Promise((resolve) => setTimeout(resolve, intervalMs));
  }
  throw new Error(`Snapshot ${snapshotId} was not ready within the polling limit`);
}

async function downloadSnapshot(apiKey, snapshotId) {
  return apiRequest(
    apiKey,
    `${API_BASE}/snapshot/${encodeURIComponent(snapshotId)}?format=json`,
  );
}

function firstValue(record, names) {
  for (const name of names) {
    const value = record?.[name];
    if (value === undefined || value === null || value === "") continue;
    if (Array.isArray(value) || typeof value === "object") continue;
    return value;
  }
  return null;
}

function normalizeRecord(record, platform, snapshotId) {
  return {
    platform,
    url: firstValue(record, ["url", "post_url", "video_url", "shortcode_url", "link"]),
    title: firstValue(record, ["title", "caption", "description", "text"]),
    published_at: firstValue(record, ["date_posted", "published_at", "upload_date", "timestamp", "created_at"]),
    views: firstValue(record, ["views", "view_count", "views_count", "video_view_count", "play_count", "plays", "video_play_count"]),
    likes: firstValue(record, ["likes", "like_count", "likes_count"]),
    comments: firstValue(record, ["num_comments", "comment_count", "comments_count", "comments"]),
    shares: firstValue(record, ["shares", "num_shares", "share_count", "shares_count"]),
    saves: firstValue(record, ["saves", "save_count", "saves_count"]),
    reach: firstValue(record, ["reach", "accounts_reached"]),
    followers: firstValue(record, ["followers", "followers_count"]),
    snapshot_id: snapshotId,
    fetched_at: new Date().toISOString(),
    raw: record,
  };
}

async function loadUrls(args) {
  if (args.url) return [args.url];
  if (!args.urls_file) throw new Error("Use --url or --urls-file");
  const raw = JSON.parse(await fs.readFile(path.resolve(args.urls_file), "utf8"));
  if (!Array.isArray(raw)) throw new Error("URLs file must contain a JSON array");
  return raw.map((item) => (typeof item === "string" ? item : item.url)).filter(Boolean);
}

async function writeOutput(outputPath, value) {
  const resolved = path.resolve(outputPath);
  await fs.mkdir(path.dirname(resolved), { recursive: true });
  await fs.writeFile(resolved, `${JSON.stringify(value, null, 2)}\n`, "utf8");
  return resolved;
}

function usage() {
  return `Direct Bright Data Scraper API runner\n\n` +
    `Existing snapshot (no new scrape):\n` +
    `  node brightdata_api_pipeline.mjs snapshot --snapshot-id <id> --platform instagram --output <file>\n\n` +
    `New API collection:\n` +
    `  node brightdata_api_pipeline.mjs scrape --platform instagram --url <url> --output <file>\n` +
    `  node brightdata_api_pipeline.mjs scrape --platform facebook --urls-file urls.json --output <file>\n\n` +
    `Platforms: ${Object.keys(DATASETS).join(", ")}\n`;
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  if (["help", "--help", "-h"].includes(args.command)) {
    process.stdout.write(usage());
    return;
  }

  const platform = String(args.platform ?? "instagram").toLowerCase();
  if (!DATASETS[platform]) throw new Error(`Unsupported platform: ${platform}`);
  const apiKey = await loadApiKey();
  let snapshotId;

  if (args.command === "snapshot") {
    snapshotId = args.snapshot_id;
    if (!snapshotId) throw new Error("--snapshot-id is required");
  } else if (args.command === "scrape") {
    const urls = await loadUrls(args);
    snapshotId = await triggerCollection(apiKey, platform, urls);
  } else {
    throw new Error(`Unknown command: ${args.command}`);
  }

  const progress = await waitUntilReady(apiKey, snapshotId);
  const downloaded = await downloadSnapshot(apiKey, snapshotId);
  const records = Array.isArray(downloaded) ? downloaded : [downloaded];
  const normalized = records.map((record) => normalizeRecord(record, platform, snapshotId));
  const output = {
    status: "ready",
    platform,
    dataset_id: DATASETS[platform],
    snapshot_id: snapshotId,
    record_count: normalized.length,
    progress,
    records: normalized,
  };

  const outputPath = args.output ?? path.join("outputs", "brightdata", `${snapshotId}.json`);
  const savedTo = await writeOutput(outputPath, output);
  process.stdout.write(`${JSON.stringify({ status: "ready", snapshot_id: snapshotId, record_count: normalized.length, output: savedTo })}\n`);
}

main().catch((error) => {
  process.stderr.write(`${error.message}\n`);
  process.exitCode = 1;
});
