# Atlas Health Screen v3: data spec, calculations, deviation alerts, scrollable 4:3 layout

Revision of v2 (2026-09-07). Data inventory and personal values are unchanged and were
queried from the live FreeReps database on atlas (`user_id = 2`) on that date.
What changed in v3:

- **Deviation system (4.4).** Every important metric is compared to the subject's own
  average with a stated favorable direction and two thresholds (WATCH, ALERT). Sleep,
  heart, running capacity and weight are covered; favorable deviations are shown too.
- **STATUS tile (4.8).** A double-width tile that compiles the deviation system into one
  state per domain (Sleep, Heart, Fitness, Weight) and one overall state, with the
  top drivers, so the screen can be read in two seconds. A deviation list sits under it.
- **Running capacity (4.7).** Speed at fixed heart rate and 1-minute heart rate recovery
  are treated as the capacity signals, with their own baselines and alerts.
- **Alert delivery (4.10).** ALERT states push an ntfy notification; WATCH does not.
- Subject facts filled in: born 2005-08-31 (age 21 on the query date), height 6'1"
  (1.854 m, which matches the HealthKit record).

Carried over from v2: heart-rate-reserve zones, Edwards TRIMP, ACWR guard, consistency
metrics, easy share, sleep debt, social jet lag, HR-minimum timing, weight tracking, body
fat excluded, scrollable layout ordered by importance.

Purpose is unchanged: a single screen on the atlas wall board, opened by the top-right
key of the LifeSmart ColoPlay dial, that answers three questions: how did I sleep, how is
my heart, and what should I change.

---

## 1. Subject and measurement context

| Item | Value | Source |
|---|---|---|
| Date of birth | 2005-08-31 (age 21 on 2026-09-07) | stated; `.env` `DOB`, age computed at runtime |
| Height | 1.854 m (6'1") | stated; matches HealthKit 2022-09-20 |
| Weight | 81.6 kg | HealthKit, 2026-01-08 (stale; refreshed daily once the scale is in use) |
| BMI | 23.8 kg/m² | computed; recomputed from `weight_7d` once the scale is live |
| Sex | not stated in the database; men's reference tables used as in v1 | confirm |
| Age-predicted max HR | 199 (220 - age) or 193 (Tanaka: 208 - 0.7 x age) | observed 200 in a run, so `HR_MAX = 200` stands |
| Watch | Apple Watch, model unknown. No SpO2 and no wrist temperature rows ever, so neither sensor exists (consistent with Apple Watch SE). | inferred |
| Wear pattern | Watch worn for runs and for sleep only. Heart rate exists on 33 of the last 60 days. | queried |
| Nights with a real sleep session (>= 3 h) per month | Mar 10, Apr 22, May 8, Jun 11, Jul 15, Aug 9, Sep 1 (partial) | queried |
| Scale | Etekcity smart scale (Bluetooth or Wi-Fi model TBD) writing to Apple Health through the VeSync app | planned |

The wear pattern is the single most important constraint. Any metric Apple computes per
calendar day from all-day wear (steps, stand hours, active calories, move ring) is biased
on this subject and is excluded from the screen.

---

## 2. Available data

Everything below is in Postgres on atlas, schema `public`, readable by the `analysis`
role. All timestamps are `timestamptz`; local time is `America/New_York`.

### 2.1 Tables used

| Table | Rows (user 2) | What it holds | Quirks found |
|---|---|---|---|
| `health_metrics` | ~574 k | One row per HealthKit sample: `time, metric_name, source, units, qty, min_val, avg_val, max_val` | `source` is the empty string for every companion-app row. `heart_rate` rows carry `avg_val`/`min_val`/`max_val` and **NULL `qty`**; every other metric carries `qty`. Always read `COALESCE(qty, avg_val)`. Weight rows from VeSync will carry a non-empty `source`; confirm the string after the first weigh-in. |
| `sleep_stages` | 6 661 | `start_time, end_time, stage, duration_hr, source` | Watch source is the literal string `Dominick’s Apple Watch` (curly apostrophe U+2019). iPhone source `Dominick Dupuy` writes only `In Bed`. Watch stages start 2026-01-26. |
| `sleep_sessions` | 1 315 | Nightly rollup built by a FreeReps server backfill | Splits some nights into fragments (in the last 60 days, 20 sessions >= 3 h and 10 fragments < 3 h). Not used; see 4.1. |
| `workouts` | 122 | One row per workout with `name, start_time, end_time, duration_sec, distance (m), avg/max/min_heart_rate, active_energy_burned, is_indoor, location` | Names are normalized: `Running`, `Traditional Strength Training`, `Walking`, `Rowing`, ... |
| `workout_heart_rate` | 5 533 | 1-minute HR buckets per workout: `time, workout_id, min_bpm, avg_bpm, max_bpm` | Used for zones, Edwards TRIMP, and speed at fixed HR. |
| `workout_routes` | 41 693 | GPS points for 43 workouts | Confirms outdoor runs have GPS. |
| `activity_summaries` | 1 090 | Apple's daily ring totals | Excluded (all-day wear bias). |
| `import_logs` | 430+ | One row per ingest request | Feeds the coverage footer only. |

### 2.2 Metrics present in the last 60 days (2026-07-09 to 2026-09-07)

| metric_name | rows | days with data | Value column | Use |
|---|---|---|---|---|
| `heart_rate` | 7 866 | 33 | `avg_val` | sleeping HR, HR-minimum timing |
| `heart_rate_variability` (SDNN, ms) | 96 | 30 | `qty` | nightly HRV mean, 7-night mean |
| `resting_heart_rate` | 19 | 19 | `qty` | Apple's daily RHR (only on watch days); zone anchor |
| `respiratory_rate` (breaths/min) | 1 021 | 27 | `qty` | nightly mean during sleep |
| `walking_heart_rate_average` | 17 | 17 | `qty` | context only |
| `heart_rate_recovery_one_minute` | 4 (24 since Jan 2026) | 4 | `qty` | capacity signal |
| `running_speed` (m/s) | 2 256 | 5 | `qty` | speed at fixed HR (coverage is thin; see 4.7) |
| `physical_effort` | 4 475 | 36 | `qty` | not used (opaque Apple scale) |
| `weight_body_mass` (kg) | 55 rows since 2017, none in window | 0 | `qty` | weight (4.5), populated by the scale from now on |
| `distance_walking_running`, `step_count`, `flights_climbed`, `active_energy`, `basal_energy_burned`, `apple_exercise_time`, `apple_stand_time` | hourly buckets | 28 to 60 | `qty` | **excluded** (all-day wear bias) |
| `walking_speed`, `walking_step_length`, `walking_asymmetry_percentage`, `walking_double_support_percentage`, `apple_walking_steadiness` | many | 60 | `qty` | iPhone gait metrics, excluded |
| `headphone_audio_exposure`, `environmental_audio_exposure` | many | 26 to 58 | `qty` | excluded |
| `six_minute_walk_test_distance` | 3 | 3 | `qty` | excluded (iPhone estimate) |
| `body_fat_percentage`, `body_mass_index`, `lean_body_mass` | last written 2024 | 0 | | **excluded by decision**, even once the scale writes them |

### 2.3 Workouts in the window

| Period | Running | Strength | Other |
|---|---|---|---|
| Last 60 days | 6 runs, 15.9 km, 129 min total, avg HR 159 | 2 sessions | 0 |
| Last 365 days | 40 runs, 95.7 km, 36 outdoor / 4 indoor | 31 all-time | Rowing, Downhill Skiing, Walking |

Recent runs (all outdoor with GPS unless noted):

| Date | Distance | Duration | Avg HR | Max HR | 1-min HR recovery |
|---|---|---|---|---|---|
| 2026-07-25 | 0.54 km (indoor) | 9 min | 145 | 177 | none |
| 2026-08-24 | 3.25 km | 29 min | 149 | 191 | 15.5 |
| 2026-08-26 | 3.23 km | 36 min | 143 | 192 | none |
| 2026-08-27 | 1.74 km | 11 min | 170 | 196 | 35.9 |
| 2026-09-01 | 3.24 km | 17 min | 183 | 198 | 29.8 |
| 2026-09-05 | 3.88 km | 28 min | 162 | 194 | 28.0 |

Highest heart rate recorded in any run in the last 365 days: **200 bpm**.

---

## 3. Missing and unreliable data

| Gap | Evidence | Consequence | Fix or workaround |
|---|---|---|---|
| **VO2 max: zero rows, ever** | The companion app maps VO2 max under Mobility, and other Mobility metrics did sync, so sync is not the problem. Apple has produced no estimate. Apple requires an Outdoor Walk/Run/Hike >= 20 min, < 5 % grade, good GPS, HR about 30 % above resting, and a Health profile with date of birth and sex. | Cannot show VO2 max. | Check Health > Browse > Heart > Cardio Fitness. If empty, confirm date of birth and sex in Health Details, then do one 20+ min outdoor run on flat ground. Meanwhile the screen uses the capacity signals (4.7) and a VDOT-style estimate as proxies. |
| Blood oxygen, wrist temperature | No rows ever | No SpO2 or temperature tiles. | Hardware limitation. |
| Workout effort score | No rows | No effort tile; Edwards TRIMP instead. | Not needed. |
| Resting heart rate on only 19 of 60 days | Apple computes RHR only on days the watch is worn during quiet wake time. | RHR is blank most days. | Sleeping HR is the primary heart metric (available every worn night); Apple RHR is secondary. Zone anchor uses the 60-day RHR mean, so it never depends on today's value. |
| Nights split into fragments | 10 of 30 FreeReps sessions in 60 days are < 3 h | Naive averages understate sleep by about 20 %. | Re-cluster stages with a 2 h gap rule; longest cluster is the night, others are naps. |
| Watch not worn on about 45 % of nights | 20 real nights in the last 60 | Rolling means must skip missing nights. Coverage must be displayed. | Rolling windows are defined over nights with data; hollow markers for missing nights. |
| Sex not confirmed | Not in HealthKit export | VO2 max and HRV bands differ by sex. | Men's tables used; confirm. |
| Body weight stale | Last reading 2026-01-08 | No trend until the scale is in use. | Scale writes daily; weight block shows "n readings" until 7 exist. |
| `running_speed` present on only 5 days | 2 256 rows in 60 days | Speed at fixed HR is only computable for those runs. | Fall back to the efficiency factor when no speed samples exist for a run; label which one is shown. |
| Heart rate recovery on 4 of 6 recent runs | Apple records it only when the watch stays on and still for a minute after "End" | Capacity signal has gaps. | Keep the watch on for one minute after ending a run. |
| No blood pressure, no ECG, no glucose | Tables exist but are empty | Out of scope. | None. |
| HRV night-to-night scatter is very wide | SD 28 ms on a 79 ms mean in 60 days | A single night's HRV is noise. | Only the 7-night rolling mean is shown as a number; flag statistics use the SD of 7-night means (4.4). |

---

## 4. Calculations

Conventions used everywhere:

- **Night with data**: a night whose re-clustered session (4.1) has `asleep_min >= 180`.
- **Windows** ("7n", "14n", "60d") are counted over nights with data and end at the most
  recent night with data, never at today.
- **Baseline** = mean and SD over the 60-day window, over nights with data (or runs with
  data, for run metrics). `z = (value - mean) / SD`.
- **7-night mean statistics**: whenever a 7-night mean is compared to a baseline, the
  baseline SD is the SD of the rolling 7-night means across the window, not the SD of
  single nights. (Single-night SD is roughly 2.5 times larger and would make those
  comparisons unreachable.)
- **Run** = `workouts.name = 'Running'`. **Valid run** = run, `is_indoor = false`,
  `duration_sec >= 900`. Capacity and trend metrics use valid runs only; counts use all
  runs.
- **Strength** = `workouts.name = 'Traditional Strength Training'` (plus any name in
  `STRENGTH_NAMES`).
- Config in `.env`: `DOB=2005-08-31`, `HEIGHT_M=1.854`, `SEX`, `HR_MAX` (default 200),
  `SLEEP_GOAL_H` (default 7.5), `RUNS_PER_WEEK_TARGET` (3), `STRENGTH_PER_WEEK_TARGET`
  (2), `EASY_SHARE_TARGET` (0.70), `FIXED_HR_BAND` (150 to 160), `FREE_DAYS` (Sat, Sun),
  `NTFY_TOPIC`.

### 4.1 Sleep sessions from stages

Input: `sleep_stages` where `user_id = 2`, `source = 'Dominick’s Apple Watch'`,
`stage <> 'In Bed'`, last 70 days.

1. Sort by `start_time`. Start a new session when the gap from the previous `end_time`
   exceeds 2 h.
2. `night_date` = local date of the session end (a night ending 07:10 on Sep 5 is Sep 5).
3. If several sessions share a `night_date`, the longest is the night; the rest sum into
   `naps_min`.
4. Per night: `asleep_min` = Core + Deep + REM (+ `Asleep` if present); `deep_min`,
   `rem_min`, `core_min`, `awake_min`; `interruptions` = count of Awake segments strictly
   between the first and last asleep segment; `sleep_start` = first asleep segment start;
   `sleep_end` = last asleep segment end.
5. `deep_pct = deep_min / asleep_min`, `rem_pct` likewise.

### 4.2 Sleep timing and quantity

1. `bedtime` = local clock of `sleep_start` on a 24 h scale shifted so 00:00 to 12:00
   counts as 24 to 36 (00:40 = 24.67). `waketime` = local clock of `sleep_end`.
   `midsleep = (bedtime + waketime_shifted) / 2` on the same scale.
2. `bedtime_dev_min` = |bedtime - median bedtime of the previous 13 nights with data|, in
   minutes. `bedtime_sd_min` = SD of bedtime over the last 14 nights with data.
3. `sleep_7n` = mean `asleep_min` over the last 7 nights with data.
   `sleep_vs_avg_h = sleep_7n - 60d mean asleep`, the "vs average" number on the tile.
4. **Sleep debt**: `debt_14n_h = sum over the last 14 nights with data of
   max(0, SLEEP_GOAL_H - asleep_h)`; `debt_per_night_h = debt_14n_h / 14`. Because missing
   nights are skipped, the debt is a lower bound and is labelled as such.
5. **Social jet lag**: over the last 28 nights with data,
   `sjl_h = |median midsleep on free nights - median midsleep on work nights|`, free night
   = `night_date` in `FREE_DAYS`. Requires at least 3 nights of each kind; otherwise blank.
6. **Target bedtime** (feeds the action line):
   `target_bedtime = median waketime (60d) - SLEEP_GOAL_H - 20 min`, rounded to 5 min.
7. **Sleep score estimate**, 0 to 100:
   `dur = 40 * min(asleep_h / SLEEP_GOAL_H, 1) + 5 * min(deep_h / 1.0, 1) + 5 * min(rem_h / 1.5, 1)`;
   `cons = 30 * max(0, 1 - bedtime_dev_min / 90)`;
   `intr = 20 * max(0, 1 - interruptions / 8)`.
   Parts shown separately; the lowest is named the "biggest lever".

### 4.3 Heart during sleep

For each night, from `health_metrics` where `metric_name = 'heart_rate'` and `time`
between `sleep_start` and `sleep_end`:

1. `sleep_avg_hr = avg(COALESCE(qty, avg_val))`;
   `sleep_min_hr = min(COALESCE(qty, min_val, avg_val))`.
2. **HR-minimum timing (heuristic)**: 5-minute rolling median of the HR samples inside
   the night; `hr_min_frac = (t_min - sleep_start) / (sleep_end - sleep_start)`.
   Bands: < 0.5 early, 0.5 to 0.7 mid, > 0.7 late (commonly follows late meals, alcohol,
   or a hard evening workout). Consumer-wearable heuristic, shown as a dot per night in
   the sleep-detail block; contributes one WATCH signal, never an ALERT.
3. `hrv_night = avg(qty)` of `heart_rate_variability` samples between 18:00 local of the
   previous day and 12:00 local of `night_date`. `hrv_7n` = mean over the last 7 nights
   with data.
4. `resp_night = avg(qty)` of `respiratory_rate` samples in the same window.
5. `rhr_day = last qty` of `resting_heart_rate` on `night_date`, may be NULL.
   `rhr_60d` = mean of `resting_heart_rate` over the last 60 days (currently 53); zone
   anchor and TRIMP fallback.

### 4.4 Deviation system: baselines, direction, WATCH and ALERT

This is the core of "tell me if something is up". Every signal below has:

- a **statistic** (what is compared),
- a **baseline** (the subject's own average and spread, window stated),
- a **favorable direction** (which way is good; deviations the other way are
  unfavorable),
- **WATCH** and **ALERT** thresholds. WATCH means "worth noticing, no action required
  by itself". ALERT means "act today" and pushes a notification (4.10).
- a **minimum data** requirement below which the signal is blank, never OK.

States per signal: OK, WATCH, ALERT, and GOOD (favorable deviation past the WATCH
distance, shown in green, never pushes). Where both a relative (z) and an absolute
threshold are given, whichever fires first counts.

**Sleep**

| Signal | Statistic | Baseline | Favorable | WATCH | ALERT | Min data |
|---|---|---|---|---|---|---|
| S1 sleep, latest night | `asleep_h` | 60d mean, single-night SD | above | z < -1 or < 6.0 h | 3 or more of the last 7 nights < 6.0 h | 7 nights |
| S2 sleep, 7-night mean | `sleep_7n` | 60d mean, SD of 7-night means | above | `sleep_vs_avg_h` < -0.5 h | < -1.0 h, or `debt_14n_h` > 7 h | 7 nights |
| S3 bedtime regularity | `bedtime_sd_min` (14n) | none (absolute) | below | > 45 min | > 60 min | 7 nights |
| S4 interruptions | latest night | 60d mean, single-night SD | below | z > 1 | z > 2 | 14 nights |
| S5 sleep architecture | `deep_pct`, `rem_pct` (7n) | none (absolute) | inside band | deep < 10 % or REM < 15 % | never (informational) | 7 nights |
| S6 HR-minimum timing | `hr_min_frac` (7n) | none (absolute) | below | 4 or more of 7 nights > 0.7 | never | 7 nights |

**Heart**

| Signal | Statistic | Baseline | Favorable | WATCH | ALERT | Min data |
|---|---|---|---|---|---|---|
| H1 sleeping HR | `sleep_avg_hr`, latest night | 60d mean, single-night SD | below | z > 1 | z > 2, or z > 1 on 3 consecutive nights with data | 14 nights |
| H2 HRV | `hrv_7n` | 60d mean, **SD of 7-night means** | above | z7 < -1 | z7 < -2 | 14 nights |
| H3 respiratory rate | `resp_night`, latest night | 60d mean, single-night SD | below | z > 1 | z > 2, or absolute > 20 | 14 nights |
| H4 resting HR (Apple) | `rhr_day` when present | none (absolute) | below | >= 62 | >= 70 | 1 day |
| H5 nightly HR minimum | `sleep_min_hr` | 60d mean, single-night SD | below | z > 2 | never | 14 nights |
| **Composite ILLNESS / OVERREACHING** | two or more of H1, H2, H3 at WATCH or worse on the same night | | | | ALERT, outranks everything | |

**Fitness (running capacity, 4.7)**

| Signal | Statistic | Baseline | Favorable | WATCH | ALERT | Min data |
|---|---|---|---|---|---|---|
| F1 speed at fixed HR | latest valid run vs mean of the previous 5 runs with a value | previous 5 runs | above | < -3 % | < -6 %, or 3 consecutive declines | 3 runs |
| F2 heart rate recovery | `hrr_1min`, latest run | personal mean and SD of readings since Jan 2026 | above | < 20, or z < -1 | < 15 | 5 readings |
| F3 load spike | `load_week` vs `load_4w_avg` (Edwards TRIMP) | 4-week average | inside band | > 1.5 x | > 2.0 x | 8 workouts in 28 d, else informational |
| F4 run consistency | `days_since_run` | target | below | > 7 / target + 1 (3.3 d at target 3) | > 7 d | none |
| F5 strength consistency | `days_since_strength` | target | below | > 7 / target + 1 (4.5 d at target 2) | > 10 d | none |
| F6 easy share | `easy_share_60d` | target 0.70 | above | < 0.50 | never (shape, not safety) | 3 runs |
| F7 capacity index | mean of z(F1), z(F2), each vs its own baseline | | above | < -1 | < -2 | both present |

**Weight**

| Signal | Statistic | Baseline | Favorable | WATCH | ALERT | Min data |
|---|---|---|---|---|---|---|
| W1 weight drift | `weight_7d - weight_28d` | 28-day mean | inside band | |delta| > 1.0 kg | |delta| > 2.0 kg (unintentional) | 3 of 7 and 8 of 28 readings |
| W2 weigh-in coverage | `weigh_ins_7d` | | | < 3 | never | none |

Favorable deviations that are shown as GOOD: S2 > +0.5 h, H2 z7 > +1, F1 > +3 %,
F2 z > +1, H1 z < -1 (sleeping HR clearly below average).

### 4.5 Weight

Source: `health_metrics` where `metric_name = 'weight_body_mass'` (kg). VeSync writes
through Apple Health; the companion app syncs it like any other quantity. Body fat,
BMI-from-scale, and lean mass rows that VeSync also writes are ignored.

1. `weight_day` = median of readings per local date (guards against a double weigh-in).
2. `weight_7d` = trailing mean of `weight_day` over the last 7 calendar days, shown only
   when at least 3 readings exist; otherwise the last reading with its date and
   "n = k / 7".
3. `weight_28d` = trailing mean over 28 days (>= 8 readings), else blank.
4. `weight_wk_change = weight_7d - weight_7d as of 7 days earlier`. Arrow when
   |change| > 0.3 kg.
5. `bmi = weight_7d / HEIGHT_M²` (1.854² = 3.437), displayed small under the tile.
6. `weigh_ins_7d` = number of days with a reading in the last 7.

Weigh-in protocol assumed: same time each morning, after the bathroom, before food.

### 4.6 Exercise consistency

Counts use all workouts regardless of validity.

1. `days_since_run`, `days_since_strength`.
2. `runs_per_week_4w` = runs in the last 28 days / 4; `strength_per_week_4w` likewise.
3. `runs_this_week`, `strength_this_week` (ISO week, Mon to Sun).
4. `longest_run_gap_60d` (context).

### 4.7 Exercise intensity, load, and running capacity

**Zones (heart rate reserve, Karvonen).** `HRR = HR_MAX - rhr_60d`. Z1 < 60 %,
Z2 60 to 70 %, Z3 70 to 80 %, Z4 80 to 90 %, Z5 >= 90 % of HRR, added to `rhr_60d`.
With `HR_MAX = 200` and `rhr_60d = 53`: Z1 < 141, Z2 141 to 156, Z3 156 to 171,
Z4 171 to 185, Z5 >= 185 bpm. Zone minutes per workout from `workout_heart_rate.avg_bpm`
per 1-minute bucket. Edges are recomputed nightly and printed on the screen.

**Easy share.** `easy_share_60d = (Z1 + Z2 minutes) / total run minutes` over 60 days,
and per run. Every run since 2026-08-27 is Z3 or harder, so this starts well under the
0.70 target.

**Training load (Edwards TRIMP).** Per workout
`trimp = 1*Z1min + 2*Z2min + 3*Z3min + 4*Z4min + 5*Z5min`. `load_week` = current ISO
week; `load_4w_avg` = last 28 days / 4.

**ACWR guard.** `acwr = load_7d / (load_28d / 4)` is displayed only when the 28-day
window contains at least 8 workouts (at 0.7 runs per week, one run gives about 1.4 and
two give about 2.8, which would flag the behaviour the subject should increase). Below
the threshold the screen shows "this week X, 4-wk avg Y". When shown: 0.8 to 1.3 neutral,
> 1.5 red, always a caution band.

**Pace.** Per run: distance, min/mile (primary) and min/km.

**Running capacity: two signals and an index.**

1. *Speed at fixed heart rate (F1).* For each valid run with `running_speed` samples:
   bucket speed to the same 1-minute grid as `workout_heart_rate` (mean per minute), keep
   minutes with `avg_bpm` inside `FIXED_HR_BAND` and mean speed > 1.5 m/s, take the
   median speed. Requires 5 such minutes. `speed_at_hr_vs_avg_pct` = latest value vs the
   mean of the previous 5 runs with a value, in percent. `speed_at_hr_trend` = OLS slope
   over the last 10 runs with a value, shown up / flat / down with a +/- 1 % per run dead
   band. Isolates the aerobic curve better than any ratio using session-average HR.
2. *Heart rate recovery (F2).* `hrr_1min` = the `heart_rate_recovery_one_minute` sample
   within 10 min after `end_time`. Personal baseline: mean and SD of all readings since
   2026-01 (currently 24 readings, mean 32.7). The last four are shown on the screen.
3. *Capacity index (F7).* `capacity_z = mean(z(F1), z(F2))` where z(F1) uses the SD of
   `speed_at_hr_vs_avg_pct` over the last 10 runs and z(F2) the personal HRR SD. Shown as
   CAPACITY with an arrow. It exists so that the Fitness chip in the STATUS tile has one
   number behind it; the two components are always shown next to it.
4. *Durability (context).* Longest valid run in the last 28 days vs the last 60.

**Efficiency factor (fallback).** `ef = (distance_m / duration_min) / avg_heart_rate`,
valid runs only. Used for F1 only when fewer than 4 of the last 10 valid runs have a
speed-at-HR value; the block labels which metric is shown.

**VDOT-style estimate (optional, low priority).** From the fastest valid run of at least
3 km in the last 90 days, with `v` in m/min and `t` in minutes:
`vo2 = -4.60 + 0.182258*v + 0.000104*v²`,
`pct = 0.8 + 0.1894393*exp(-0.012778*t) + 0.2989558*exp(-0.1932605*t)`,
`vdot = vo2 / pct`. Labelled "est."; reads low because a training run is not an all-out
effort; disappears when Apple's VO2 max appears.

### 4.8 STATUS tile: compiling the deviation system

The STATUS tile is the first thing on the screen and the only place that summarizes
everything.

1. **Domain state** = the worst state among the domain's signals (Sleep: S1 to S6;
   Heart: H1 to H5 plus the composite; Fitness: F1 to F7; Weight: W1, W2). A signal that
   is blank for lack of data does not count. If every signal in a domain is blank, the
   domain shows NO DATA, not OK.
2. **Overall state** = the worst domain state. The illness/overreaching composite forces
   Heart and Overall to ALERT.
3. **Drivers** = the two signals with the highest severity, ties broken by |z| (or by the
   size of the percent deviation for F1). GOOD signals are listed only when no WATCH or
   ALERT exists.
4. **Tile content**, three lines:
   - Line 1: `STATUS  WATCH` in the board's large weight, colored by state.
   - Line 2: four chips: `Sleep WATCH · Heart OK · Fitness WATCH · Weight OK`. Each chip
     colored by its domain state; NO DATA in the muted weight.
   - Line 3, muted: the drivers as "metric value (vs avg)", for example
     `sleep 7n 5h50 (-0.7 h) · speed@155 -4 %`.
5. **Deviation list** (in the action strip under the tiles): up to 4 lines sorted by
   severity then |z|, each formatted `metric  value  vs avg  arrow  STATE`, for example
   `sleeping HR  54  +3.9 bpm (z 1.0)  ^  WATCH`. At most one GOOD line, last.
6. The STATUS tile never shows raw numbers other than the drivers; the domain tiles and
   blocks do.

### 4.9 Action line

Exactly one line, chosen by the first matching rule, top to bottom. Later matches become
lines in the deviation list.

| Priority | Condition | Line |
|---|---|---|
| 1 | Illness/overreaching composite | "Rest today. Sleeping HR +X bpm, HRV -Y % vs your average." |
| 2 | S1 or S2 at ALERT | "Bed by {target_bedtime} tonight. Debt {debt_14n_h} h over 14 nights." |
| 3 | S3 at ALERT | "Bed by {target_bedtime} tonight. Bedtime varies +/-{bedtime_sd_min} min; aim within 30." |
| 4 | F1 or F7 at ALERT | "Back off: speed at {FIXED_HR_BAND} down {x} % over 5 runs. Next run easy, under {Z2 top}." |
| 5 | F4 at WATCH or ALERT | "Run today, easy (HR under {Z2 top}). {days_since_run} days since last run." |
| 6 | F5 at WATCH or ALERT | "Lift today. {days_since_strength} days since last session." |
| 7 | F6 at WATCH and a run is due this week | "Next run easy: keep HR under {Z2 top}. Easy share {easy_share_60d} % vs 70 %." |
| 8 | W1 at ALERT | "Weight {weight_7d} kg, {delta} kg vs 28-day mean. Check intake and weigh-in timing." |
| 9 | none | "On track. Next: {whichever of run/lift is due first}." |

### 4.10 Alert delivery

- The nightly job (10:00) computes the deviation system and the status.
- If Overall = ALERT, push to `NTFY_TOPIC` at 10:05 with the action line plus the
  deviation list (max 5 lines). Push again only if a new signal reaches ALERT or the
  state has been ALERT for 3 consecutive days (then every third day).
- WATCH and GOOD never push. The screen is where they live.
- Sunday 20:00: a weekly summary push with sleep 7n vs 60d, capacity, runs and strength
  counts vs target, weight 7d vs 28d. Optional, off by default.

### 4.11 Coverage

`nights_with_data_7`, `nights_with_data_60`, `weigh_ins_7d`, `last_import_at` (max
`import_logs.created_at` with status success), `last_hr_sample_at`, `last_weight_at`.

---

## 5. Health assessment against reference ranges

Personal values are from the database as of 2026-09-07. "365d" uses nights with a >= 3 h
session. Reference ranges are cited in section 6. Age 21 on the query date.

| Metric | Your value | Reference range | Reading |
|---|---|---|---|
| Resting HR (Apple daily) | 60d mean 53, median 52, SD 8.8, range 39 to 72 (19 days); 365d mean 55, SD 6.3 (161 days) | 60 to 100 normal adult; 40 to 60 typical for endurance-trained | **Good.** Athlete range, no symptoms reported. Days at 70+ are worth correlating with short sleep, alcohol, or illness. The 39 is either a very restful day or a sensor artifact. |
| Sleeping HR | mean 50.1, SD 3.8, nightly minimum mean 44.3, lowest single reading 36 (84 nights) | 40 to 60 typical for adults asleep; trained adults commonly 40 to 50 | **Good.** A single 36 is not alarming in a runner; only symptomatic bradycardia (dizziness, fainting) needs a doctor. |
| HRV, Apple SDNN, nightly mean | 60d mean 78.6, SD 28; 365d mean 73.8, median 70.6 (179 nights) | 55 to 65 ms typical for Apple Watch users in their early 20s; ECG norms about 50 ms at 25 to 34 | **Above average for age 21, which is favourable.** The night-to-night SD of 28 ms is expected from Apple's sampling. Use the 7-night trend, not the level. |
| Respiratory rate asleep | 365d mean 16.9, SD 1.3, range 14.5 to 25.5 | 12 to 20 normal; most WHOOP users 13 to 18 | **Normal.** Nights above 20 (max 25.5) usually mark illness or alcohol; H3 catches these. |
| Sleep duration | 365d mean 6.42 h, SD 1.72 (96 nights); 60d mean 6.59 h; 6 of 20 nights < 6 h; 7 of 20 nights >= 7 h | AASM: >= 7 h; NSF: 7 to 9 h for ages 18 to 25 | **Below recommendation.** Roughly one night in three is under 6 h and only a third reach 7 h. The clearest finding in the data. |
| Deep sleep share | 15.8 % (365d), 15.6 % (60d) | 13 to 23 % of total sleep; higher in the early 20s | **Normal.** |
| REM share | 22.0 % (365d), 22.7 % (60d) | 20 to 25 % | **Normal.** |
| Bedtime regularity | mean bedtime 00:40, SD 77 min, range 22:16 to 02:57 (60d, 20 nights) | Sleep-onset SD <= 30 min lowest risk in adult cohorts; regularity predicted mortality more strongly than duration in UK Biobank (ages 40 to 69); each +1 h of SD about +18 % CVD hazard in MESA (ages 45 to 84) | **Irregular.** The cohort hazard ratios come from middle-aged and older adults and should not be read as a personal risk number at 21. What transfers directly is the effect on sleep quality, next-day alertness, and the difficulty of getting 7 h with a bedtime that moves by over an hour. Second clearest finding. |
| Sleep duration variability | SD 1.72 h (365d) | Duration SD <= 60 min lowest risk in the same cohorts | **High.** Partly a wear artifact (short-wear nights), which the 2 h re-clustering reduces; expect it to remain above 60 min. |
| 1-min heart rate recovery | 24 readings since Jan 2026, mean 32.7; last four 15.5, 35.9, 29.8, 28.0 | <= 12 bpm abnormal (Cole 1999); > 20 fit; > 30 very fit | **Good.** Only the 2026-08-24 reading (15.5) was low; it followed a 3-week gap with no runs. |
| Max HR observed | 200 bpm (365d) | Age-predicted 199 (220 - 21) or 193 (Tanaka) | Consistent with age 21. `HR_MAX = 200`. |
| VO2 max | none | Men 20 to 29: < 41.7 poor, median 48, > 55.4 superior (Cooper/ACSM) | **Unknown.** See section 3. |
| Running intensity | last six runs avg HR 143 to 183, mean 159 (about 72 % HRR); no run since 2026-08-27 below Z3 | Polarized or pyramidal training: 70 to 80 % of run time easy (Z1 to Z2) | **All hard, no easy.** Not dangerous at this volume, but the wrong shape for improving VO2 max and resting HR. F6 exists to change it. |
| Running volume | about 6 runs and 16 km per 60 days; 96 km per year | For VO2 max and RHR improvement, 3 sessions/week with one >= 30 min is the usual minimum | **Low and irregular.** Fitness markers are good despite low volume, which suggests they would respond quickly to consistency. |
| Weight | 81.6 kg (2026-01-08) | | **Stale.** Refreshes daily with the scale. |
| BMI | 23.8 (from stale weight, height 1.854 m) | 18.5 to 24.9 | Normal. |

### 5.1 Personal baselines and the thresholds they imply

Computed from the values above so the reviewer can see where the lines fall. Values
marked "approx" are placeholders until the nightly job computes them from rolling means.

| Signal | Your average | Spread used | WATCH fires at | ALERT fires at |
|---|---|---|---|---|
| S1 sleep, latest night | 6.6 h (60d) | single-night SD approx 1.2 h (fragments removed) | < 6.0 h (absolute wins) | 3 of last 7 nights < 6.0 h |
| S2 sleep, 7-night mean | 6.6 h | | < 6.1 h | < 5.6 h, or debt > 7 h |
| S3 bedtime regularity | SD 77 min now | | > 45 min (already firing) | > 60 min (already firing) |
| H1 sleeping HR | 50.1 bpm | SD 3.8 | >= 53.9 | >= 57.7, or >= 53.9 on 3 nights running |
| H2 HRV 7n | 78.6 ms | SD of 7-night means approx 11 ms | < 68 | < 57 |
| H3 respiratory rate | 16.9 /min | SD 1.3 | > 18.2 | > 19.5, or > 20 |
| H4 resting HR | 53 (60d) | absolute | >= 62 | >= 70 |
| F1 speed at 150 to 160 | not yet computed | previous 5 runs | -3 % | -6 % |
| F2 HR recovery | 32.7 bpm | SD approx 7 (24 readings) | < 26 or < 20 | < 15 |
| F4 run consistency | 0.7 runs/week now | target 3/week | > 3.3 days | > 7 days (2 days since the 09-05 run on the query date, so OK) |
| F6 easy share | approx 10 % now | target 70 % | < 50 % (already firing) | never |
| W1 weight drift | no data | 28-day mean | +/- 1.0 kg | +/- 2.0 kg |

Reading the table: on the query date the STATUS tile would show Sleep ALERT (S3 at
77 min), Heart OK, Fitness WATCH (F6), Weight NO DATA, so Overall ALERT driven by
bedtime regularity, and the action line would be rule 3: "Bed by 23:50 tonight. Bedtime
varies +/-77 min; aim within 30." That matches the human reading of the data in 5.2, and
it means the first ntfy push fires on day one; that is intended.

### 5.2 Summary for the subject

Heart: nothing in the data suggests a problem. Resting HR, sleeping HR, HRV, respiratory
rate and heart-rate recovery are all in the trained-healthy band, and HRV is above the
typical range for age 21. Watch for: a sustained rise in sleeping HR with a fall in HRV
(the composite in 4.4), and any run where 1-min recovery drops under 15.

Sleep: the two things that are measurably off are duration (mean 6.4 h, a third of nights
under 6 h) and timing (bedtime SD 77 min). Sleep architecture (deep, REM) is normal, so
the fix is behavioral: an earlier and more consistent bedtime. The screen turns this into
a target bedtime and an alert.

Fitness: heart-rate recovery and max HR indicate good aerobic health for the training
volume. The runs are all hard and there are too few of them; the consistency and
easy-share signals exist to fix both, and the capacity signals will show whether it works.
Getting a VO2 max estimate requires one flat 20+ min outdoor run and a complete Health
profile (date of birth and sex).

Weight: no current data. Once the scale is in use the 7-day mean is the number to watch;
single readings are not.

Caveats: the subject wears the watch on roughly half of nights, so every sleep statistic
is a sample of chosen nights, likely the more routine ones. Apple HRV is a spot SDNN, not
a 5-minute clinical recording, and Apple sleep staging is a wrist estimate (agreement with
polysomnography is moderate for Deep and REM). The HR-minimum timing, sleep score, easy
share target, capacity index and VDOT estimate are training heuristics, not clinical
measures. The deviation thresholds are statistical, not diagnostic: an ALERT means "your
numbers are unusual for you", not "something is wrong". None of this is medical advice.

---

## 6. Sources

- Resting HR ranges: [American Heart Association, bradycardia](https://www.heart.org/en/health-topics/arrhythmia/about-arrhythmia/bradycardia--slow-heart-rate); [Mayo Clinic, heart rate](https://www.mayoclinic.org/healthy-lifestyle/fitness/expert-answers/heart-rate/faq-20057979); [Medical News Today, athlete heart rate](https://www.medicalnewstoday.com/articles/athletes-heart-rate)
- Sleeping HR: [Sleep Foundation](https://www.sleepfoundation.org/physical-health/sleeping-heart-rate); [92 457-person wearable RHR cohort](https://www.ncbi.nlm.nih.gov/pmc/articles/PMC7001906/)
- HRV: [Empirical Health, Apple Watch HRV average](https://www.empirical.health/metrics/hrv/); [Korte, HRV by age](https://kortehealth.com/learn/hrv-by-age); [wearable HRV reference values](https://www.researchgate.net/publication/393471933_Normal_reference_values_of_heart_rate_variability_HRV_for_short-term_recordings_of_R-R_intervals_by_age_group_gender_physical_activity_race_obtained_on_wearable_smart_devices_smart_watches_smart_brace)
- Sleep duration: [AASM adult sleep duration advisory](https://aasm.org/advocacy/position-statements/adult-sleep-duration-health-advisory/); [Sleep Foundation, how much sleep](https://www.sleepfoundation.org/how-sleep-works/how-much-sleep-do-we-really-need)
- Sleep stages: [Sleep Foundation, deep sleep](https://www.sleepfoundation.org/stages-of-sleep/deep-sleep); [Sleep Foundation, stages of sleep](https://www.sleepfoundation.org/stages-of-sleep)
- Respiratory rate: [Sleep Foundation, respiratory rate while sleeping](https://www.sleepfoundation.org/sleep-apnea/sleep-respiratory-rate); [WHOOP, respiratory rate](https://www.whoop.com/us/en/thelocker/what-is-respiratory-rate-normal/)
- Sleep regularity: [Windred et al. 2024, SLEEP](https://academic.oup.com/sleep/article/47/1/zsad253/7280269); [Huang et al. 2020, JACC, MESA](https://www.jacc.org/doi/10.1016/j.jacc.2019.12.054) and [PMC full text](https://pmc.ncbi.nlm.nih.gov/articles/PMC7237955/)
- Social jet lag: Wittmann et al. 2006, Chronobiology International, "Social jetlag: misalignment of biological and social time"
- Heart rate recovery: [Cole et al. 1999, NEJM](https://www.nejm.org/doi/full/10.1056/NEJM199910283411804); [GE HealthCare overview](https://www.gehealthcare.com/en-us/insights/article/heart-rate-recovery-and-risk-understanding-this-powerful-marker)
- Max HR prediction: Tanaka et al. 2001, JACC, "Age-predicted maximal heart rate revisited"
- VO2 max norms and Apple's method: [ACSM/Cooper tables](https://afasteryou.com/blog/en/vo2max-table/); [Apple Support, cardio fitness](https://support.apple.com/en-us/108790); [Apple white paper, VO2 max estimation](https://www.apple.com/healthcare/docs/site/Using_Apple_Watch_to_Estimate_Cardio_Fitness_with_VO2_max.pdf)
- VDOT formulas: Daniels and Gilbert 1979, "Oxygen Power: Performance Tables for Distance Runners"
- Heart rate reserve zones: Karvonen et al. 1957; ACSM Guidelines for Exercise Testing and Prescription (HRR method)
- Edwards TRIMP: Edwards 1993, "The Heart Rate Monitor Book"
- Training intensity distribution: Seiler 2010, IJSPP, "What is best practice for training intensity and duration distribution in endurance athletes?"
- Training load: [Gabbett 2016, BJSM](https://www.researchgate.net/publication/290431785_The_training-injury_prevention_paradox_Should_athletes_be_training_smarter_and_harder); [Science for Sport, ACWR](https://www.scienceforsport.com/acutechronic-workload-ratio/); the "sweet spot" figure has been [criticized](https://www.researchgate.net/publication/333589357_The_acute-chronic_workload_ratio-injury_figure_and_its_'sweet_spot'_are_flawed), hence the guard and the caution-band presentation
- Illness and overreaching signals from wearables: the combination of elevated sleeping HR, reduced HRV and elevated respiratory rate is the pattern used by Oura and WHOOP readiness models and reported in COVID-19 wearable studies (e.g. Mishra et al. 2020, Nature Biomedical Engineering); thresholds here are the subject's own z-scores, not those models

---

## 7. Planned screen (4:3 panel, vertical scroll)

The atlas board is a fixed-height 1080 px canvas whose width follows the panel's aspect
ratio, so on this 1024x768 monitor it renders at **1440x1080** logical pixels, scaled to
fit. The existing header (brand, drawn ColoPlay, clock) stays and is sticky; the health
screen replaces the `main` region below it. Type scale, panel chrome, glow and colors
follow `board.css`. State colors: OK muted, GOOD green, WATCH amber, ALERT red, NO DATA
muted italic.

Trigger: ColoPlay top-right key (Shift+Alt+Digit2) shows the health screen and lights
key 2; top-left key (Digit1) returns to the ops board. Dial rotation scrolls the main
region; any scroll resets a 60 s timer after which the screen snaps back to the top, and
the screen returns to ops on its own after 10 minutes. The dial's round screen shows
`HEALTH` while active.

Design rule for scrolling: everything needed to answer the three questions is above the
fold (the first 984 px under the header). Scrolling only reveals detail and secondary
domains, never something the subject must see.

Layout, in logical pixels, inside the 1440-wide canvas with the board's 24 px gutters.
The tile row is 7 units wide; STATUS takes 2 units (about 398 px), the other five one
unit each (about 199 px).

```
+- header (existing, sticky, 96 px) --------------------------------------------------------+

=== ABOVE THE FOLD (984 px) ================================================================

+- tiles row, 128 px -----------------------------------------------------------------------+
| STATUS  ALERT                      | SLEEP 6h12 v | SLEEP HR 49 - | HRV 7n 81 ^ | WEIGHT 7d -- | LAST RUN 2d  |
| Sleep ALERT . Heart OK .           | 7n 6h35      | 60d 50 .      | 60d 79      | no data      | 0.7/wk       |
| Fitness WATCH . Weight NO DATA     | -0.2 h vs avg| min 43        | +/-11       | n 0/7        | tgt 3/wk     |
| bedtime SD 77 min . easy share 10 %|              |               |             |              |              |
+- action strip, 160 px --------------------------------------------------------------------+
| > Bed by 23:50 tonight. Bedtime varies +/-77 min; aim within 30.                           |
|   bedtime regularity   77 min    vs 30 target        ^   ALERT                             |
|   easy share           10 %      vs 70 % target      v   WATCH                             |
|   sleep 7n             6h35      -0.2 h vs avg       -   OK                                |
|   HRV 7n               81 ms     +2 ms (z7 +0.2)     -   OK          o watch worn 4/7      |
+- sleep block, 2 columns, ~600 px ---------------------------------------------------------+
| LEFT 900 px                              | RIGHT 468 px                                    |
| SLEEP . 60 NIGHTS                        | SLEEP SCORE  71                                 |
| stacked bars deep/REM/core per night,    | duration 33/50 . consistency 18/30 <- lever     |
| awake gaps as notches, 7-night mean      | interruptions 20/20 . "bed by 23:50"            |
| line, goal line at SLEEP_GOAL_H, hollow  | (about 200 px)                                  |
| marker on nights without watch,          | SLEEP HR + HRV sparklines, 60 nights, 60d       |
| WATCH/ALERT nights tinted                | mean and +/-1 SD band (HRV band from            |
|                                          | 7-night-mean SD) (about 200 px)                 |
|                                          | RESTING 52 (60d 53) . RESP 16.4 . HRR 28        |
|                                          | (last 4: 16 36 30 28) (about 120 px)            |

=== BELOW THE FOLD (scroll) ================================================================

+- exercise block, ~600 px -----------------------------------------------------------------+
| LEFT 900 px                              | RIGHT 468 px                                    |
| CAPACITY  -  (speed@155 --, HRR z +0.3)  | RUNS . LAST 8                                   |
| SPEED @ 150-160: 2.9 m/s, vs prev 5      | date, mi, pace, avg/max HR, HRR,                |
| runs +1 % (n 6 of 10)                    | speed@155 or EF, zone bar per run;              |
| HR RECOVERY: 28 (mean 32.7, SD 7)        | WATCH/ALERT runs tinted                         |
| CONSISTENCY: runs/wk 0.7 (tgt 3) .       |                                                 |
| strength/wk 0.5 (tgt 2) . this week 1/0  |                                                 |
| EASY SHARE 60d: 10 % (tgt 70 %)          | VO2 est. (VDOT) 39, from 2026-09-01,            |
| zone edges: Z2 141-156, Z3 156-171 ...   | small, labelled est.                            |
| LOAD: 8 weekly Edwards-TRIMP bars,       |                                                 |
| this week 180 . 4-wk avg 95 .            |                                                 |
| ACWR hidden (n=8 in 28 d needed)         |                                                 |
+- weight block, ~260 px -------------------------------------------------------------------+
| WEIGHT . 60 DAYS: daily readings as dots, 7-day mean line, 28-day mean dashed;             |
| right margin: 7d -- . 28d -- . week -- . BMI 23.8 (stale) . weigh-ins 0/7                  |
+- sleep detail block, ~320 px -------------------------------------------------------------+
| BEDTIME . WAKE strip: one thin bar per   | SOCIAL JET LAG 1h40 (free vs work)              |
| night from bedtime to wake on a 21:00 to | HR-MIN TIMING: dot per night at hr_min_frac,    |
| 10:00 axis, median bedtime line, target  | bands early/mid/late                            |
| bedtime line (110 px)                    | NAPS: minutes per day, 60 d                     |
+- coverage footer, 56 px ------------------------------------------------------------------+
| nights with data 4/7, 20/60 . weigh-ins 0/7 . last import 07:12 . last HR 06:58            |
+-------------------------------------------------------------------------------------------+
```

Tile rules: value in the board's large weight, baseline line beneath in the muted weight
with the "vs avg" number, arrow colored only when |z| > 1 (GOOD green, unfavorable amber),
red only when a signal from 4.4 is at ALERT. The STATUS tile is the only tile with chips.
Nothing on the screen is interactive beyond dial scrolling.

What is deliberately not on the screen: steps, stand hours, move ring, daily calories,
walking gait metrics, audio exposure, body fat, lean mass, scale-reported BMI, and
FreeReps' own correlation explorer (already on the FreeReps web dashboard).

---

## 8. Open questions for the reviewer

1. Sex for the VO2 max and HRV reference tables (men's used, as in v1).
2. `HR_MAX`: keep the observed 200, or replace with a measured value if one exists.
3. Confirm the 2 h gap rule and "longest session is the night" against a week of nights
   in the Health app.
4. `SLEEP_GOAL_H`: 7.5 (used for debt, target bedtime and the score) or 8.
5. Targets: 3 runs and 2 strength sessions per week, easy share 70 %.
6. `FREE_DAYS` for social jet lag: Sat/Sun, or a student schedule with other free
   mornings.
7. `FIXED_HR_BAND` 150 to 160, or lower once easy running exists.
8. WATCH/ALERT thresholds in 4.4: keep the z = 1 / z = 2 convention, or loosen S3 (bedtime
   SD) so it stops firing every day until the habit changes. Recommendation: keep it; the
   whole point is that it fires until the habit changes.
9. Whether the sleep score weights should be recalibrated against Apple's Sleep Score once
   a month of both exists.
10. Miles or kilometers as the primary running unit (miles assumed).
11. Scale model (Bluetooth vs Wi-Fi) and the VeSync `source` string once the first
    reading lands.
12. Scroll behaviour: 60 s snap-back to top and 10 min return to ops, or stay until key 1.
13. ntfy cadence for a persistent ALERT (every third day proposed).
