/**
 * PR Video Costs sheet: adds a row for every new influencer, channel or month by itself.
 *
 * Install once: in the PR Video Costs sheet open Extensions > Apps Script, replace everything with this file,
 * click Save, choose "setup" in the toolbar and click Run (allow access when asked). It then runs every morning.
 *
 * The daily dashboard refresh publishes costs_missing.csv: the source / account / month combinations that have
 * videos but no row in this sheet yet. This script appends those rows with the Amount left empty for you to fill,
 * and fills the "Profile links" column (the account's Facebook / Instagram / YouTube page) wherever it is empty.
 * It never changes or deletes anything you typed.
 */
const BASE = 'https://sagarchauhan7792-bot.github.io/pr-video-analytics/';
const MISSING_URL = BASE + 'costs_missing.csv';
const LINKS_URL = BASE + 'costs_links.csv';

function setup() {
  ScriptApp.getProjectTriggers().forEach(t => ScriptApp.deleteTrigger(t));
  ScriptApp.newTrigger('addNewAccounts').timeBased().everyDays(1).atHour(11).inTimezone('Asia/Kolkata').create();
  linksColumn_();
  inhousePerVideo_();
  addNewAccounts();
}

/** Adds a "Profile links" column right after Account (once). */
function linksColumn_() {
  const sh = sheet_(), head = sh.getRange(1, 1, 1, sh.getLastColumn()).getValues()[0], c = cols_(head);
  if (c.links >= 0) return;
  sh.insertColumnAfter(c.account + 1);
  sh.getRange(1, c.account + 2).setValue('Profile links').setFontWeight('bold');
  sh.setColumnWidth(c.account + 2, 330);
  sh.getRange(1, c.account + 2, sh.getMaxRows(), 1).setWrap(true);
}

/** Fills empty Profile links cells from the dashboard's list of each account's pages. */
function fillLinks_() {
  const res = UrlFetchApp.fetch(LINKS_URL + '?t=' + Date.now(), { muteHttpExceptions: true });
  if (res.getResponseCode() !== 200) return;
  const rows = Utilities.parseCsv(res.getContentText('UTF-8'));
  const head = rows.shift().map(norm_), at = (n) => head.indexOf(n);
  const map = new Map(rows.map(r => [norm_(r[at('source')]) + '|' + norm_(r[at('account')]), r[at('links')]]));
  const sh = sheet_(), vals = sh.getDataRange().getValues(), c = cols_(vals[0]);
  if (c.links < 0) return;
  // an account name used on more than one row (one per platform) is left for you: the list can't tell them apart
  const seen = {};
  vals.slice(1).forEach(r => { const k = norm_(r[c.source]) + '|' + norm_(r[c.account]); seen[k] = (seen[k] || 0) + 1; });
  for (let r = 1; r < vals.length; r++) {
    if (String(vals[r][c.links]).trim() || !vals[r][c.source]) continue;
    const k = norm_(vals[r][c.source]) + '|' + norm_(vals[r][c.account]);
    if (seen[k] > 1) continue;
    if (!norm_(vals[r][c.account]) && norm_(vals[r][c.source]) !== 'inhouseaivideos') continue;  // blank rows
    const links = map.get(k);
    if (links) sh.getRange(r + 1, c.links + 1).setValue(links);
  }
}

/** Inhouse ai videos are paid per video: one row for all months (only while its Amount is still empty). */
function inhousePerVideo_() {
  const sh = sheet_(), vals = sh.getDataRange().getValues(), c = cols_(vals[0]);
  for (let r = 1; r < vals.length; r++) {
    const row = vals[r];
    if (norm_(row[c.source]) === 'inhouseaivideos' && !String(row[c.account]).trim() && !String(row[c.amount]).trim()) {
      sh.getRange(r + 1, c.month + 1).setValue('');
      sh.getRange(r + 1, c.basis + 1).setValue('per video');
      if (c.notes >= 0) sh.getRange(r + 1, c.notes + 1).setValue('Cost of one inhouse video (all channels, all months)');
    }
  }
}

function addNewAccounts() {
  const res = UrlFetchApp.fetch(MISSING_URL + '?t=' + Date.now(), { muteHttpExceptions: true });
  if (res.getResponseCode() !== 200) return fillLinks_();
  const incoming = Utilities.parseCsv(res.getContentText('UTF-8'));
  const head = incoming.shift().map(norm_);
  const at = (name) => head.indexOf(name);
  const sh = sheet_(), vals = sh.getDataRange().getValues(), c = cols_(vals[0]);
  const have = new Set(vals.slice(1).map(r => key_(r[c.source], r[c.account], r[c.month])));
  const add = [];
  incoming.forEach(r => {
    const src = r[at('source')], acc = r[at('account')], mon = r[at('month')];
    if (!src || have.has(key_(src, acc, mon))) return;
    have.add(key_(src, acc, mon));
    const row = new Array(vals[0].length).fill('');
    row[c.source] = src; row[c.account] = acc; row[c.month] = monthDate_(mon); row[c.basis] = r[at('basis')] || 'per month';
    if (c.notes >= 0) row[c.notes] = 'Added automatically: ' + r[at('videos')] + ' videos, fill in the amount';
    add.push(row);
  });
  if (add.length) {
    const start = sh.getLastRow() + 1;
    sh.getRange(start, 1, add.length, add[0].length).setValues(add);
    try { sh.getRange(start, c.month + 1, add.length, 1).setNumberFormat('mmm yyyy'); } catch (e) {}  // a typed (table) column keeps its own format
  }
  fillLinks_();
}

function sheet_() { const ss = SpreadsheetApp.getActive(); return ss.getSheetByName('Video Cost') || ss.getSheets()[0]; }

function cols_(head) {
  const h = head.map(norm_), find = (...names) => h.findIndex(x => names.some(n => x.startsWith(n)));
  return { source: find('source'), account: find('account'), month: find('month'), amount: find('amount'),
           basis: find('basis'), notes: find('note'), links: find('profilelink', 'link') };
}

function norm_(v) { return String(v).toLowerCase().replace(/[^\p{L}\p{M}\p{N}]+/gu, ''); }

function month_(v) {
  if (v instanceof Date) return Utilities.formatDate(v, 'Asia/Kolkata', 'yyyy-MM');
  const s = String(v).trim();
  if (!s) return '';
  const d = new Date('1 ' + s);
  return isNaN(d) ? s.toLowerCase() : Utilities.formatDate(d, 'Asia/Kolkata', 'yyyy-MM');
}

/** "Nov 2026" stays text: the sheet turns it into 1 Nov 2026 in its own time zone (a date made here would use the
 * script's time zone and could land on 31 Oct). Blank stays blank. */
function monthDate_(s) {
  return String(s || '').trim();
}

function key_(src, acc, mon) { return norm_(src) + '|' + norm_(acc) + '|' + month_(mon); }

/** One-time fix (28 Oct 2026): sets the profile link of each row in the Video Cost tab, matched by account and amount,
 * so two rows with the same name (one per platform) are told apart. It replaces the links the daily fill had guessed
 * for these rows and clears the list it put on rows that have no account. */
function fillVideoCostLinks() {
  linksColumn_();
  const L = [
    ['Radhika', 4000, 'https://www.facebook.com/radhikavloglife/'],
    ['Astha Gupta', 4000, 'https://www.youtube.com/@astha_2715/'],
    ['Jyoti', 4000, 'https://www.facebook.com/people/Mommy-and-Baby/61576927244714/'],
    ['Masoom Chauhan', 6500, 'https://www.facebook.com/profile.php?id=100019057946966'],
    ['Ruchi Yadav', 5500, 'https://www.facebook.com/people/Ruchi-Yadav/100006565786989/'],
    ['PVF Art', 5000, 'https://www.facebook.com/priyamvadaavashisthafilms/'],
    ['VP Pandey', 5500, 'https://www.facebook.com/vindeswariprasad.pandey/'],
    ['India First', 8000, 'https://www.facebook.com/hindustan24newsnetwork/'],
    ['Kalpana', 5500, 'https://www.facebook.com/kalpanajha2/'],
    ['Numberdaar', 6000, 'https://www.facebook.com/people/Numberdar-%E0%A4%96%E0%A4%AC%E0%A4%B0-%E0%A4%A6%E0%A4%AE%E0%A4%A6%E0%A4%BE%E0%A4%B0/61589587457070/\nhttps://www.youtube.com/@TheNumberdarNews/'],
    ['Indra Pandey', 3000, 'https://www.facebook.com/vindeshwari.prasad.pandey.pandey.2025/'],
    ['Diyam Aksht', 3000, 'https://www.facebook.com/DhyanAkshat/'],
    ['NATIONAL KHABAR', 11000, 'https://www.facebook.com/nationalkhabarr/'],
    ['NATIONAL KHABAR', 8000, 'https://www.instagram.com/nationalkhabardigital/'],
    ['Jagrti Dubey', 8000, 'https://www.facebook.com/jagriti.dubey.9256/'],
    ['Vande Matram', 5000, 'https://www.facebook.com/atharv.mishra.827708/'],
    ['Dharmendra Kumar', 5000, 'Post: https://www.facebook.com/share/v/1BqfF9r5f6/'],
    ['Sonu Kumari', 6000, 'Post: https://www.facebook.com/share/r/1KYDB48FyF/'],
    ['Chinta Devi', 6000, 'https://www.facebook.com/nidhi.devi.161282'],
    ['Adarsh Jha', 5500, 'https://www.instagram.com/adarsh24_jha_/'],
    ['Adarsh Jha', 6000, 'https://www.facebook.com/adarshtalks/'],
    ['Adarsh Jha', 5000, 'https://www.facebook.com/profile.php?id=100005206926761'],
    ['Devender Mor', 5000, 'https://www.facebook.com/people/Devender-Mor/100082569219080/'],
    ['Seraching for Swad', 5000, 'https://www.facebook.com/profile.php?id=100066854509368'],
    ['The Right', 8000, 'https://www.instagram.com/therighthindi/'],
    ['Shiv upadhyay', 8000, 'https://www.instagram.com/shiv.sh.upadhyay/'],
    ['Pathakraj News', 4000, 'https://www.facebook.com/profile.php?id=61590515015994'],
    ['Pathakraj News', 4000, 'https://www.instagram.com/newspathakraj/'],
    ['Bharatnama News', 5000, 'https://www.instagram.com/bharatnamanews/'],
    ['Infility News', 7000, 'https://www.instagram.com/infinity_news24/']
  ];
  const sh = sheet_(), vals = sh.getDataRange().getValues(), c = cols_(vals[0]);
  const used = new Set();
  let n = 0;
  const out = [];
  for (let r = 1; r < vals.length; r++) {
    const acc = norm_(vals[r][c.account]), amt = Number(String(vals[r][c.amount]).replace(/[^0-9.]/g, ''));
    const src = norm_(vals[r][c.source]);
    if (!acc && src && src !== 'inhouseaivideos') { sh.getRange(r + 1, c.links + 1).setValue(''); out.push('row ' + (r + 1) + ': cleared'); continue; }
    const i = L.findIndex((x, k) => !used.has(k) && norm_(x[0]) === acc && x[1] === amt);
    if (i >= 0) { used.add(i); sh.getRange(r + 1, c.links + 1).setValue(L[i][2]); n++; out.push('row ' + (r + 1) + ': ' + vals[r][c.account] + ' ' + amt); }
  }
  SpreadsheetApp.flush();
  Logger.log('links set: ' + n + '\n' + out.join('\n'));
  return n;
}
