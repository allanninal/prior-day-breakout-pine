# SPDX-License-Identifier: LicenseRef-PolyForm-Noncommercial-1.0.0 (see LICENSE)
"""Python reference of pine/PriorDayBreakout.pine, bar for bar, with a model of TradingView's broker emulator on bar
data. The script must give the same signals, levels and trades (tests/check_pine.py).

Rule:
  * server day: time + shift hours in the day time zone (the tests use UTC and 0: the price files are in server time);
  * levels: high and low of the previous trading day that has bars, at most 7 calendar days back; the chart's first
    (usually cut) day never serves as a previous day;
  * signal: on a bar opening at 01:00 or later, close > previous-day high (buy) or close < previous-day low (sell),
    once per side per day; a side is used up by its first signal even when no trade follows;
  * entry: at the signal bar's close (Pine process_orders_on_close), when no position is open and the close time
    (time_close) is on the same day and before 20:00; stop = the other side of the range, target = entry + R x risk;
  * exit: stop or target from the next bar on, or at the close of the first bar whose close time is 23:00 or later on
    the entry day (or any later day).

Broker emulator model (TradingView's documented OHLC assumption): from the open the price goes first to the extreme
nearer the open (high when high - open < open - low, else low), then to the other extreme, then to the close. An order
whose price was passed at the open (a gap) fills at the open; otherwise a stop or limit fills at its own price.
"""
from dataclasses import dataclass

import numpy as np

DAY_MS = 86_400_000
FROM_MIN, TO_MIN, FLAT_MIN, MAX_GAP = 60, 20 * 60, 23 * 60, 7


@dataclass
class Trade:
    side: int
    entry_bar: int
    entry_price: float
    qty: float
    exit_bar: int = -1
    exit_price: float = float("nan")
    why: str = ""


def server_clock(t_ms, shift_h=0):
    """Day numbers and minutes of the day of UNIX-ms times, for a UTC day shifted by shift_h hours."""
    s = np.asarray(t_ms, dtype=np.int64) + shift_h * 3_600_000
    return s // DAY_MS, (s % DAY_MS) // 60_000


def levels(t, h, l):
    """Previous-day high and low per bar (nan when none), and the day number of each bar."""
    day, _ = server_clock(t)
    n = len(t)
    ph = np.full(n, np.nan)
    pl = np.full(n, np.nan)
    first = day[0]
    cur = None
    chi = clo = np.nan
    pday = None
    phi = plo = np.nan
    for i in range(n):
        if day[i] != cur:
            if cur is not None and cur != first:
                pday, phi, plo = cur, chi, clo
            cur, chi, clo = day[i], h[i], l[i]
        else:
            chi, clo = max(chi, h[i]), min(clo, l[i])
        if pday is not None and day[i] - pday <= MAX_GAP:
            ph[i], pl[i] = phi, plo
    return ph, pl, day


def _path(o, h, l, c):
    """The bar's assumed price path: open, nearer extreme, other extreme, close."""
    return (o, h, l, c) if (h - o) < (o - l) else (o, l, h, c)


def run(t, o, h, l, c, tf_ms, target_r=2.0, qty=1.0):
    """Signals per bar (+1/-1/0), previous-day levels and the trade list."""
    t = np.asarray(t, np.int64)
    n = len(t)
    ph, pl, day = levels(t, h, l)
    _, mins = server_clock(t)
    end_day, end_min = server_clock(t + tf_ms)
    sig = np.zeros(n, int)
    trades = []
    pos = None
    done = {1: False, -1: False}
    cur_day = None
    for i in range(n):
        # 1. the stop and target working from earlier bars fill inside this bar
        if pos is not None and i > pos["entry_bar"]:
            s = pos["side"]
            path = _path(o[i], h[i], l[i], c[i])
            prev = path[0]
            for k, p in enumerate(path):
                lo_, hi_ = (min(prev, p), max(prev, p)) if k else (p, p)
                events = []
                if (s > 0 and lo_ <= pos["stop"]) or (s < 0 and hi_ >= pos["stop"]):
                    events.append((abs(pos["stop"] - prev) if k else 0.0, "stop", pos["stop"] if k else p))
                if (s > 0 and hi_ >= pos["target"]) or (s < 0 and lo_ <= pos["target"]):
                    events.append((abs(pos["target"] - prev) if k else 0.0, "target", pos["target"] if k else p))
                if events:
                    _, why, price = min(events, key=lambda e: e[0])
                    trades.append(Trade(s, pos["entry_bar"], pos["entry"], pos["qty"], i, price, why))
                    pos = None
                    break
                prev = p
        # 2. the script at the bar's close
        if day[i] != cur_day:
            cur_day = day[i]
            done = {1: False, -1: False}
        sg = 0
        if not np.isnan(ph[i]) and mins[i] >= FROM_MIN:
            if c[i] > ph[i] and not done[1]:
                done[1], sg = True, 1
            elif c[i] < pl[i] and not done[-1]:
                done[-1], sg = True, -1
        sig[i] = sg
        if sg != 0 and pos is None and end_day[i] == day[i] and end_min[i] < TO_MIN:
            stop = pl[i] if sg > 0 else ph[i]
            risk = (c[i] - stop) * sg
            if risk > 0:
                pos = dict(side=sg, entry=c[i], qty=qty, stop=stop, target=c[i] + sg * target_r * risk, entry_bar=i, day=day[i])
        elif pos is not None and i > pos["entry_bar"] and (end_day[i] > pos["day"] or end_min[i] >= FLAT_MIN):
            trades.append(Trade(pos["side"], pos["entry_bar"], pos["entry"], pos["qty"], i, c[i], "flat"))
            pos = None
    if pos is not None:
        trades.append(Trade(pos["side"], pos["entry_bar"], pos["entry"], pos["qty"], -1, float("nan"), "open"))
    return dict(signal=sig, prev_high=ph, prev_low=pl, trades=trades)
