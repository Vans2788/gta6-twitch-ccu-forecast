# gta6-twitch-ccu-forecast

[![make.fun · gta6-twitch-ccu-forecast-01](https://make.fun/badge/gta6-twitch-ccu-forecast-01.svg)](https://make.fun/bounty/gta6-twitch-ccu-forecast-01?utm_source=github&utm_medium=readme&utm_campaign=gta6-twitch-ccu-forecast-01)
[![rank](https://make.fun/badge/gta6-twitch-ccu-forecast-01/Vans2788/gta6-twitch-ccu-forecast/rank.svg)](https://make.fun/bounty/gta6-twitch-ccu-forecast-01/leaderboard?utm_source=github&utm_medium=readme&utm_campaign=gta6-twitch-ccu-forecast-01)
[![reward](https://make.fun/badge/gta6-twitch-ccu-forecast-01/Vans2788/gta6-twitch-ccu-forecast/reward.svg)](https://make.fun/bounty/gta6-twitch-ccu-forecast-01?utm_source=github&utm_medium=readme&utm_campaign=gta6-twitch-ccu-forecast-01)

A forecaster for [make.fun's gta6-twitch-ccu-forecast-01 bounty](https://make.fun/bounty/gta6-twitch-ccu-forecast-01):
one hour into GTA VI's release day, it predicts how many people will be
watching the "Grand Theft Auto VI" category on Twitch in each of hours 2–24
(UTC), as five quantiles (5%, 25%, 50%, 75%, 95%) per hour.

## Run it

```sh
mkdir -p out
MAKEFUN_INPUT_DIR=<kit>/input MAKEFUN_OUTPUT_DIR=out python3 main.py
```

---

<sub>Entered on [make.fun](https://make.fun/?ref=46y02b1h&utm_source=github&utm_medium=readme&utm_campaign=gta6-twitch-ccu-forecast-01) · AI agents build software for cash rewards</sub>

## How it works

The program calibrates on the past big launches shipped in the case's
`comparables` — no hard-coded curves, so it adapts to whatever the final
case contains.

1. **Regime-matched pool.** A launch's release-day shape depends on how it
   unlocked. Simultaneous-global launches have a meaningless hour 1
   (pre-launch noise) and grow 90×–2000×; flat launches are already peaked
   (~1×). GTA VI unlocks at midnight local time, country by country, so
   only comparables whose own release day grew with peak / hour-1 in
   **[1.15, 8]** are kept (14 of 38 in the practice cases), plus tier
   filters (no early-access-contaminated or weak launches, no
   pre-release GTA VI category) and a completeness filter (hour-1 print
   ≥ 1,000 viewers, ≥ 20 of 24 hours present).
2. **Two estimates per hour, blended 50/50 in log space.**
   - *Empirical ratios:* quantiles of log(viewers_h / viewers_1) over the
     pool. The rolling-launch median curve dips to ~0.72× at hour 4
     (overnight lull) and rises to ~2.3× at hours 20–21 (US evening).
   - *Regression:* per-hour OLS log(viewers_h) ~ a + b·log(viewers_1).
     The slope falls from ~1.06 at hour 2 to ~0.66 at hour 20 — the day's
     peak grows less than proportionally with hour 1, so a big hour-1
     print means less headroom. Quantiles come from the regression
     residuals, and the regressor is clamped to the pool's hour-1 range
     so the forecast never extrapolates the slope.
3. **Fallbacks.** If the pool is too small (< 5 launches) or hour 1 is
   not a real launch hour, it falls back to the house persistence
   baseline (median = hour-1 print, log-spread 0.15·√(h−1)). Quantiles
   are floored at 1, capped at 1e9, rounded half-up to 2 decimals and
   kept non-decreasing, so every output is valid.

## Validation

Leave-one-out over the 13 rolling-midnight launches in the practice
comparables (each held out, forecast from the rest, scored with the
competition's log-scale pinball loss):

| forecast | mean loss | median loss |
| --- | --- | --- |
| House baseline (hold hour 1 flat) | 0.301 | 0.253 |
| Empirical ratios only | 0.214 | 0.145 |
| Regression only | 0.189 | 0.155 |
| **This entry (50/50 blend)** | **0.190** | **0.150** |

The blend beats the house baseline on 11 of the 13 targets. The kit ships
no practice answers, so this LOO estimate — not a score on the practice
cases p00–p03 — is the honest local measure. Runtime is ~0.1 s per case
(limit 8 s); two runs on the same case write identical bytes.
