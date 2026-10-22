# SPDX-License-Identifier: LicenseRef-PolyForm-Noncommercial-1.0.0 (see LICENSE)
"""Check pine/PriorDayBreakout.pine against tests/reference.py with PineTS (a local Pine v6 runtime that has a strategy
broker emulator).

    cd tests/runner && npm install && cd ../..
    python3 tests/check_pine.py <bars dir>
<bars dir> holds <SYMBOL>_<TF>.csv files with the columns time (unix ms of the SERVER clock), open, high, low, close,
oldest first; TF is M15, H1 or H4.

Levels and signals: the previous-day high/low and the signal the script plots (data window) equal the reference's on
every bar. Trades: the closed (and open) trades of PineTS's broker emulator equal the reference's: entry bar, side,
entry price, exit bar, exit price and size. Repaint: the script is re-run on the series cut at three points; every bar
before a cut must read the same, and every trade closed before the cut must be the same trade.
The script runs with the server day as UTC + 0 hours (the price files are in server time), and with commission and
slippage set to 0 before the run (the reference models no costs)."""
import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from reference import run  # noqa: E402

PINE = HERE.parent / "pine" / "PriorDayBreakout.pine"
RUNNER = HERE / "runner" / "run_pine.mjs"
TF_MS = {"M15": 15 * 60_000, "H1": 3_600_000, "H4": 4 * 3_600_000}
TF_PINE = {"M15": "15", "H1": "60", "H4": "240"}
PLOTS = ["Signal (+1 buy, -1 sell)", "Level: previous-day high", "Level: previous-day low"]
CLOCK = {"Day time zone": "UTC", "+ hours": 0}
CUSTOM = {"Settings": "The target below"}
# tag: (PineTS inputs keyed by input title, reference target R, reference size). PineTS does not know the chart
# symbol, so the validated targets (2 R on H1, 1 R on M15) are given explicitly.
CONFIGS = {
    "target_2R": ({**CUSTOM, "Target, x the stop distance (R)": 2.0}, 2.0, 1.0),
    "target_1R_3_units": ({**CUSTOM, "Target, x the stop distance (R)": 1.0, "Units per trade": 3}, 1.0, 3.0),
}


def testable():
    """The script with commission and slippage at 0."""
    src = PINE.read_text(encoding="utf-8")
    src = re.sub(r"commission_value = [\d.]+", "commission_value = 0", src)
    src = re.sub(r"slippage = \d+", "slippage = 0", src)
    t = tempfile.NamedTemporaryFile(suffix=".pine", delete=False, mode="w", encoding="utf-8")
    t.write(src)
    t.close()
    return Path(t.name)


def group(rows):
    """Trades keyed by (entry bar, side, exit bar, exit price), size summed."""
    g = defaultdict(float)
    ent = {}
    for eb, s, ep, xb, xp, q in rows:
        k = (int(eb), int(s), int(xb), None if xp != xp else round(float(xp), 6))
        g[k] += q
        ent[k] = round(float(ep), 6)
    return {k: (ent[k], round(v, 4)) for k, v in g.items()}


def pine_run(pine, bars_csv, tf, inputs):
    """(plots DataFrame, trades) of one PineTS run."""
    with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as t:
        out = t.name
    tr = out + ".json"
    cmd = ["node", str(RUNNER), str(pine), str(bars_csv), out, "--tf", TF_PINE[tf], "--trades", tr]
    for k, v in {**CLOCK, **inputs}.items():
        cmd += ["--input", f"{k}={str(v).lower() if isinstance(v, bool) else v}"]
    subprocess.run(cmd + PLOTS, check=True, capture_output=True, text=True)
    d = pd.read_csv(out)
    j = json.loads(Path(tr).read_text())
    os.unlink(out)
    os.unlink(tr)
    rows = [(t["entry_bar_index"], 1 if t["size"] > 0 else -1, t["entry_price"], t["exit_bar_index"], t["exit_price"], abs(t["size"]))
            for t in j["closed"]]
    rows += [(t["entry_bar_index"], 1 if t["size"] > 0 else -1, t["entry_price"], -1, float("nan"), abs(t["size"])) for t in j["open"]]
    return d, group(rows)


def same(a, b):
    return (np.isnan(a) & np.isnan(b)) | (np.abs(np.nan_to_num(a) - np.nan_to_num(b)) < 1e-9)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("bars")
    a = ap.parse_args()
    series = sorted(Path(a.bars).glob("*.csv"))
    pine = testable()
    ok = True
    for tag, (inputs, rr, qty) in CONFIGS.items():
        nbars = nsig = bad_lv = bad_sig = ntr = bad_tr = 0
        why = defaultdict(int)
        for f in series:
            tf = f.stem.split("_")[-1]
            b = pd.read_csv(f)
            d, got = pine_run(pine, f, tf, inputs)
            r = run(b.time.to_numpy(np.int64), *(b[k].to_numpy(float) for k in ("open", "high", "low", "close")), TF_MS[tf], rr, qty)
            exp = group([(t.entry_bar, t.side, t.entry_price, t.exit_bar, t.exit_price, t.qty) for t in r["trades"]])
            nbars += len(b)
            sg = d[PLOTS[0]].fillna(0).astype(int).to_numpy()
            nsig += int((r["signal"] != 0).sum())
            bad_sig += int((sg != r["signal"]).sum())
            for p, e in ((PLOTS[1], r["prev_high"]), (PLOTS[2], r["prev_low"])):
                miss = np.where(~same(d[p].to_numpy(float), e))[0]
                bad_lv += len(miss)
                for i in miss[:2]:
                    print(f"  {tag} {f.stem} bar {i} {p}: pine {d[p].iloc[i]} reference {e[i]}")
            ntr += len(exp)
            for t in r["trades"]:
                why[t.why] += 1
            diff = set(got.items()) ^ set(exp.items())
            bad_tr += len(diff)
            for k, _ in sorted(diff, key=lambda x: x[0])[:4]:
                print(f"  {tag} {f.stem} trade (entry bar, side, exit bar, exit price) {k}: pine {got.get(k)} reference {exp.get(k)}")
        print(f"parity {tag}: {len(series)} series, {nbars} bars, {nsig} signals, {ntr} trades; "
              f"{bad_lv} level, {bad_sig} signal, {bad_tr} trade mismatches (reference exits: "
              + ", ".join(f"{k} {v}" for k, v in sorted(why.items())) + ")")
        ok &= bad_lv == bad_sig == bad_tr == 0
    inputs = CONFIGS["target_2R"][0]
    changed = checked = tchk = tchg = 0
    for f in series:
        tf = f.stem.split("_")[-1]
        b = pd.read_csv(f)
        full, ftr = pine_run(pine, f, tf, inputs)
        n = len(b)
        for cut in (n // 2, n - 300, n - 1):
            with tempfile.NamedTemporaryFile(suffix=".csv", delete=False, mode="w") as t:
                b.iloc[:cut].to_csv(t, index=False)
            part, ptr = pine_run(pine, t.name, tf, inputs)
            os.unlink(t.name)
            for p in PLOTS:
                checked += cut
                changed += int((~same(part[p].to_numpy(float)[:cut], full[p].to_numpy(float)[:cut])).sum())
            closed_part = {k: v for k, v in ptr.items() if k[2] >= 0}
            tchk += len(closed_part)
            tchg += sum(1 for k, v in closed_part.items() if ftr.get(k) != v)
    print(f"repaint: {checked} bar values re-read after cuts, {changed} changed; {tchk} closed trades re-read, {tchg} changed")
    ok &= changed == 0 and tchg == 0
    print("ALL PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
