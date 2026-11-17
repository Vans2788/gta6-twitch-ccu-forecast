"""GTA VI Twitch CCU forecaster (make.fun gta6-twitch-ccu-forecast-01).

Reads one case (/input/<id>.in) and writes /output/<id>.out with five
quantiles (the case's levels) of Twitch concurrent viewers in the
"Grand Theft Auto VI" category for each of hours 2..24 of release day
(UTC), from the case's hour-1 print and the past launches in its
`comparables`.

Method (calibrated on the kit's practice data by leave-one-out over the
comparable launches; see README.md for the numbers):

  * The release day of a rolling-midnight console launch has a stable
    shape: an overnight dip to ~0.7x the hour-1 print around hour 4,
    then a rise to ~2.3x by hours 20-21 as the US evening arrives.
  * The pool is the comparable launches whose own release day grew
    (peak / hour-1 in [1.15, 8]) — the rolling-midnight regime. This
    excludes simultaneous-global launches (hour 1 is pre-launch noise,
    growth 90x-2000x) and already-peaked launches (flat, ~1x), both of
    which would corrupt the forecast for a rolling launch.
  * Two estimates per hour, blended 50/50 in log space:
      V1: empirical quantiles of log(viewers_h / viewers_1) over the pool.
      V4: per-hour OLS log(viewers_h) ~ a + b * log(viewers_1); the
          slope falls from ~1.06 at hour 2 to ~0.66 at hour 20, i.e. the
          day's peak grows less than proportionally with hour 1, so a
          big hour-1 print means less headroom. Quantiles come from the
          regression residuals. The regressor is clamped to the pool's
          hour-1 range so the forecast never extrapolates the slope.
  * Fallbacks keep every case valid: if the pool is too small (< 5) or
    hour 1 is not a real launch hour, fall back to the house persistence
    baseline (median = hour-1 print, log-spread 0.15 * sqrt(h - 1)).

Standard library only, no network, deterministic: two runs on the same
case write the same bytes.
"""

import json
import math
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# The grader's -P leaves /bot off sys.path: put it back first, so a module
# shipped beside this file imports. (This entry is a single file.)
sys.path.insert(0, str(Path(__file__).resolve().parent))

INPUT_DIR = Path(os.environ.get("MAKEFUN_INPUT_DIR", "/input"))
OUTPUT_DIR = Path(os.environ.get("MAKEFUN_OUTPUT_DIR", "/output"))

# Pool filter: a comparable launch counts as a rolling-midnight launch
# when its own release day grew this much over its hour-1 print.
GROWTH_LO = 1.15
GROWTH_HI = 8.0
# A comparable needs a real hour-1 print and a mostly complete day.
MIN_H1 = 1000.0
MIN_DAY_HOURS = 20
MIN_POOL = 5
# Blend weight of the empirical-ratio forecast (V1); the rest is the
# regression forecast (V4).
BLEND_V1 = 0.5
# House persistence baseline's log-spread per sqrt hour (fallback only).
BASELINE_SIGMA = 0.15


def parse_ts(s):
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def round2(x):
    """x rounded to 2 decimals, half up, as JavaScript's Math.round does."""
    scaled = x * 100
    whole = math.floor(scaled)
    if scaled - whole >= 0.5:
        whole += 1
    value = whole / 100
    return int(value) if float(value).is_integer() else value


def norm_ppf(p):
    """Inverse standard normal CDF (Acklam's rational approximation)."""
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285984469534e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00]
    plow = 0.02425
    phigh = 1 - plow
    if p < plow:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
            ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    if p > phigh:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
            ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    q = p - 0.5
    r = q * q
    return (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q / \
        (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1)


def quantile(sorted_vals, q):
    """Linear-interpolation quantile of an ascending list."""
    n = len(sorted_vals)
    if n == 0:
        return None
    if n == 1:
        return sorted_vals[0]
    pos = q * (n - 1)
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return sorted_vals[lo]
    frac = pos - lo
    return sorted_vals[lo] * (1 - frac) + sorted_vals[hi] * frac


def release_day_curve(comp):
    """dict hour (1..24) -> viewers for a comparable's day_start."""
    ds = parse_ts(comp["day_start"])
    out = {}
    for p in comp.get("hourly", []):
        try:
            t = parse_ts(p["from"])
        except (KeyError, ValueError):
            continue
        h = int((t - ds).total_seconds() // 3600) + 1
        v = p.get("viewers")
        if 1 <= h <= 24 and v is not None and v > 0:
            out[h] = v
    return out


def tier_of(comp):
    u = comp.get("unlock") or ""
    if "early-access-contaminated" in u:
        return "contaminated"
    if "Tier: weak" in u:
        return "weak"
    return "comparable"


def is_gta_pre_release(comp):
    name = comp.get("name") or ""
    return "Grand Theft Auto VI" in name and "before release" in name


def build_pool(comparables):
    """Rolling-midnight launch curves usable for the forecast."""
    pool = []
    for comp in comparables or []:
        if is_gta_pre_release(comp):
            continue
        if tier_of(comp) != "comparable":
            continue
        curve = release_day_curve(comp)
        h1 = curve.get(1)
        if h1 is None or h1 < MIN_H1:
            continue
        if len(curve) < MIN_DAY_HOURS:
            continue
        peak = max(curve.values())
        if not (GROWTH_LO <= peak / h1 <= GROWTH_HI):
            continue
        pool.append(curve)
    return pool


def baseline_quantiles(p1, h, levels):
    """House persistence baseline: median = hour-1 print, widening spread."""
    sigma = BASELINE_SIGMA * math.sqrt(h - 1)
    return [max(1.0, p1 * math.exp(norm_ppf(tau) * sigma)) for tau in levels]


def empirical_quantiles(p1, pool, h, levels):
    """V1: quantiles of log(viewers_h / viewers_1) over the pool."""
    logs = sorted(math.log(c[h] / c[1]) for c in pool if h in c)
    if not logs:
        return None
    return [max(1.0, p1 * math.exp(quantile(logs, tau))) for tau in levels]


def regression_quantiles(p1, pool, h, levels):
    """V4: per-hour OLS log(viewers_h) ~ a + b * log(viewers_1), with
    residual quantiles. The regressor is clamped to the pool's range."""
    pts = [(math.log(c[1]), math.log(c[h])) for c in pool if h in c]
    if len(pts) < 4:
        return None
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    mx = sum(xs) / len(xs)
    my = sum(ys) / len(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx <= 0:
        return None
    sxy = sum((x - mx) * (y - my) for x, y in pts)
    b = sxy / sxx
    a = my - b * mx
    x0 = min(max(math.log(p1), min(xs)), max(xs))
    pred = a + b * x0
    res = sorted(y - (a + b * x) for x, y in pts)
    return [max(1.0, math.exp(pred + quantile(res, tau))) for tau in levels]


def forecast_case(case):
    levels = list(case["levels"])
    hours = [row["hour"] for row in case["forecast"]]
    first = next((k for k in case["known"] if k["hour"] == 1), None)
    if first is not None and first.get("viewers"):
        p1 = max(1.0, float(first["viewers"]))
    else:
        # No hour-1 print: fall back to the last history point, if any.
        hist = case.get("history") or []
        p1 = max(1.0, float(hist[-1]["viewers"])) if hist else 1.0
    pool = build_pool(case.get("comparables"))
    rows = []
    for h in hours:
        if len(pool) >= MIN_POOL:
            v1 = empirical_quantiles(p1, pool, h, levels)
            v4 = regression_quantiles(p1, pool, h, levels)
            if v1 is not None and v4 is not None:
                qs = [math.exp(BLEND_V1 * math.log(a) + (1 - BLEND_V1) * math.log(b))
                      for a, b in zip(v1, v4)]
            elif v1 is not None:
                qs = v1
            elif v4 is not None:
                qs = v4
            else:
                qs = baseline_quantiles(p1, h, levels)
        else:
            qs = baseline_quantiles(p1, h, levels)
        # Clamp, round half-up to 2 decimals, keep non-decreasing.
        qs = [min(1e9, max(1.0, q)) for q in qs]
        qs = [round2(q) for q in qs]
        run_max = 0.0
        fixed = []
        for q in qs:
            run_max = max(run_max, float(q))
            fixed.append(round2(run_max))
        rows.append({"hour": h, "q": fixed})
    return {"hours": rows}


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    for path in sorted(INPUT_DIR.glob("*.in")):
        try:
            case = json.loads(path.read_text(encoding="utf-8"))
            forecast = forecast_case(case)
        except Exception as exc:  # one bad case must not sink the rest
            print(f"skipping {path.name}: {exc}", file=sys.stderr)
            continue
        (OUTPUT_DIR / f"{path.stem}.out").write_text(
            json.dumps(forecast, separators=(",", ":")) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
