// SPDX-License-Identifier: LicenseRef-PolyForm-Noncommercial-1.0.0 (see LICENSE)
// Run a Pine v6 file with PineTS on an OHLC CSV and write the named plots as CSV (one column per plot).
//   node run_pine.mjs <file.pine> <bars.csv> <out.csv> [--input 'Title=value' ...] [--tf 60] [--trades trades.json] [plot name ...]
// Input values: true/false, numbers, or text (keyed by the input's title). --tf: the chart timeframe in Pine notation
// ("15", "60", "240", "D"); bars then carry closeTime = open + one period, so time_close is right. --trades: write a
// strategy's closed trades (and any trade still open) as JSON.
// bars.csv columns: time (unix ms), open, high, low, close[, volume]. Without plot names, every plot is written.
import { readFileSync, writeFileSync } from 'node:fs';
import { PineTS, Indicator } from 'pinets';

const [, , pineFile, barsFile, outFile, ...rest] = process.argv;
const inputs = {};
const names = [];
let tf, tradesFile;
for (let i = 0; i < rest.length; i++) {
  if (rest[i] === '--tf') tf = rest[++i];
  else if (rest[i] === '--trades') tradesFile = rest[++i];
  else if (rest[i] === '--input') {
    const [k, ...v] = rest[++i].split('=');
    const t = v.join('=');
    inputs[k] = t === 'true' ? true : t === 'false' ? false : t !== '' && !Number.isNaN(Number(t)) ? Number(t) : t;
  } else names.push(rest[i]);
}
const lines = readFileSync(barsFile, 'utf8').trim().split('\n');
const head = lines[0].split(',');
const col = (n) => head.indexOf(n);
const candles = lines.slice(1).map((ln) => {
  const v = ln.split(',');
  return {
    openTime: Number(v[col('time')]), open: Number(v[col('open')]), high: Number(v[col('high')]),
    low: Number(v[col('low')]), close: Number(v[col('close')]), volume: col('volume') >= 0 ? Number(v[col('volume')]) : 0,
  };
});
const TF_MS = { D: 86400000, W: 7 * 86400000 };
if (tf) for (const c of candles) c.closeTime = c.openTime + (TF_MS[tf] ?? Number(tf) * 60000);
const pineTS = tf ? new PineTS(candles, undefined, tf) : new PineTS(candles);
const res = await pineTS.run(new Indicator(readFileSync(pineFile, 'utf8'), inputs));
const { plots } = res;
if (tradesFile) {
  const st = res.strategy || {};
  writeFileSync(tradesFile, JSON.stringify({ closed: st.closedtrades || [], open: st.opentrades || [] }));
}
const keys = names.length ? names : Object.keys(plots);
for (const k of keys) if (!plots[k]) { console.error(`no plot "${k}"; plots: ${Object.keys(plots).join(' | ')}`); process.exit(2); }
const rows = ['bar,' + keys.map((k) => `"${k}"`).join(',')];
for (let i = 0; i < candles.length; i++) {
  rows.push(i + ',' + keys.map((k) => { const p = plots[k].data[i]; const v = p && typeof p === 'object' ? p.value : p; return v === null || v === undefined || Number.isNaN(v) ? '' : v; }).join(','));
}
writeFileSync(outFile, rows.join('\n') + '\n');
console.log(`${candles.length} bars, plots: ${keys.join(' | ')}`);
