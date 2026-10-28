/**
 * PR Video Costs sheet: adds a row for every new influencer, channel or month by itself.
 *
 * Install once: in the PR Video Costs sheet open Extensions > Apps Script, replace everything with this file,
 * click Save, choose "setup" in the toolbar and click Run (allow access when asked). It then runs every morning.
 *
 * The daily dashboard refresh publishes costs_missing.csv: the source / account / month combinations that have
 * videos but no row in this sheet yet. This script appends those rows with the Amount left empty for you to fill.
 * It never changes or deletes a row you typed.
 */
const MISSING_URL = 'https://sagarchauhan7792-bot.github.io/pr-video-analytics/costs_missing.csv';

function setup() {
  ScriptApp.getProjectTriggers().forEach(t => ScriptApp.deleteTrigger(t));
  ScriptApp.newTrigger('addNewAccounts').timeBased().everyDays(1).atHour(11).inTimezone('Asia/Kolkata').create();
  inhousePerVideo_();
  addNewAccounts();
}

/** Inhouse ai videos are paid per video: one row for all months (only while its Amount is still empty). */
function inhousePerVideo_() {
  const sh = sheet_(), vals = sh.getDataRange().getValues(), c = cols_(vals[0]);
  for (let r = 1; r < vals.length; r++) {
    const row = vals[r];
    if (norm_(row[c.source]) === 'inhouseaivideos' && !String(row[c.account]).trim() && !String(row[c.amount]).trim()) {
      sh.getRange(r + 1, c.month + 1).setNumberFormat('@').setValue('');
      sh.getRange(r + 1, c.basis + 1).setValue('per video');
      if (c.notes >= 0) sh.getRange(r + 1, c.notes + 1).setValue('Cost of one inhouse video (all channels, all months)');
    }
  }
}

function addNewAccounts() {
  const res = UrlFetchApp.fetch(MISSING_URL + '?t=' + Date.now(), { muteHttpExceptions: true });
  if (res.getResponseCode() !== 200) return;
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
    row[c.source] = src; row[c.account] = acc; row[c.month] = mon; row[c.basis] = r[at('basis')] || 'per month';
    if (c.notes >= 0) row[c.notes] = 'Added automatically: ' + r[at('videos')] + ' videos, fill in the amount';
    add.push(row);
  });
  if (!add.length) return;
  const start = sh.getLastRow() + 1;
  sh.getRange(start, c.month + 1, add.length, 1).setNumberFormat('@'); // keep "Nov 2026" as text
  sh.getRange(start, 1, add.length, add[0].length).setValues(add);
}

function sheet_() { return SpreadsheetApp.getActive().getSheets()[0]; }

function cols_(head) {
  const h = head.map(norm_), find = (...names) => h.findIndex(x => names.some(n => x.startsWith(n)));
  return { source: find('source'), account: find('account'), month: find('month'), amount: find('amount'),
           basis: find('basis'), notes: find('note') };
}

function norm_(v) { return String(v).toLowerCase().replace(/[^\p{L}\p{M}\p{N}]+/gu, ''); }

function month_(v) {
  if (v instanceof Date) return Utilities.formatDate(v, 'Asia/Kolkata', 'yyyy-MM');
  const s = String(v).trim();
  if (!s) return '';
  const d = new Date('1 ' + s);
  return isNaN(d) ? s.toLowerCase() : Utilities.formatDate(d, 'Asia/Kolkata', 'yyyy-MM');
}

function key_(src, acc, mon) { return norm_(src) + '|' + norm_(acc) + '|' + month_(mon); }
