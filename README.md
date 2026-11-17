# CanteenWise

A small tool that tells **[[person's name]]** ([[role]] at **[[place]]**, [[area/city]]) how much of each item to prepare for the next service, based on [[N]] records I collected on [[dates]].

**Screen recording (3 min):** [[link]]

## The problem
[[2-3 lines: what I saw, where, when.]]

## How it works
- `data.csv`: one row per item per service per day, recorded by hand (see `fieldwork.md` for how).
- Demand is not the same as sales. On a stockout day, demand = sold + `unmet_requests` if I counted turned-away customers; if I did not, sold is only a floor.
- The recommendation is the `--pct` percentile (default 80) of past demand, rounded up. Same-weekday history is used only if there are enough same-weekday dates (`--min-weekday-rows`, default 6); otherwise all days.
- Bad rows are rejected with a line number and reason, never silently used.

## Run
```
pip install -r requirements.txt
python canteenwise.py --csv data.csv --day Thursday --pct 80
python canteenwise.py --service lunch
```

## Real output
```
[[paste the output of your real run here]]
```

## What it gets wrong
- Stockout days with no unmet count are used at `sold`, which understates demand, so the recommendation may be too low.
- Ignores festivals, exams, rain and other one-off events.
- No trend; only past demand. Weak with a short history ([[N]] days).
- Strict check `prepared = sold + leftover + other_qty` rejects rows where I estimated; [[N]] rows were rejected.
- [[anything you saw yourself]]

## Not to be trusted for
[[e.g. event days, new menu items, any item with fewer than 3 days of data]]
