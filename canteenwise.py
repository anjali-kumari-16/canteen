"""CanteenWise: how much of each item should be prepared for the next service?

One CSV row = one food item on one service/day.

Required columns: date (YYYY-MM-DD), service, item, unit,
                  prepared, sold, leftover, stockout
Optional columns: unmet_requests, wasted, other_qty, stockout_time, note

Run:
    python canteenwise.py --csv data.csv
    python canteenwise.py --day Thursday --pct 90
    python canteenwise.py --service lunch
"""
import argparse
import datetime as dt
import math
import sys

import pandas as pd

REQUIRED_COLUMNS = ["date", "service", "item", "unit",
                    "prepared", "sold", "leftover", "stockout"]
OPTIONAL_COLUMNS = ["unmet_requests", "wasted", "other_qty",
                    "stockout_time", "note"]
WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday",
            "Friday", "Saturday", "Sunday"]
WIDTH = 110


def parse_int(value, field_name, optional=False):
    """Non-negative integer. Blank optional values become None."""
    text = str(value).strip()
    if text == "":
        if optional:
            return None
        raise ValueError(f"{field_name} is required")
    try:
        number = int(text)
    except (ValueError, TypeError):
        raise ValueError(f"{field_name} must be a whole number")
    if number < 0:
        raise ValueError(f"{field_name} cannot be negative")
    return number


def parse_bool(value, field_name):
    text = str(value).strip().lower()
    if text in {"true", "yes", "1"}:
        return True
    if text in {"false", "no", "0"}:
        return False
    raise ValueError(f"{field_name} must be true/false, yes/no, or 1/0")


def normalize_weekday(value):
    for day in WEEKDAYS:
        if value.strip().lower() == day.lower():
            return day
    raise argparse.ArgumentTypeError(
        "Enter a full weekday name, such as Monday or Thursday")


def load_data(csv_path):
    """Load and validate. Bad rows are reported, never silently used."""
    try:
        raw = pd.read_csv(csv_path, dtype=str, keep_default_na=False,
                          skipinitialspace=True)
    except FileNotFoundError:
        raise ValueError(f"CSV file not found: {csv_path}")
    except pd.errors.EmptyDataError:
        raise ValueError("The CSV file is empty")

    raw.columns = [str(c).strip().lower() for c in raw.columns]
    if len(raw.columns) != len(set(raw.columns)):
        raise ValueError("CSV contains duplicate column names")
    missing = [c for c in REQUIRED_COLUMNS if c not in raw.columns]
    if missing:
        raise ValueError("Missing required columns: " + ", ".join(missing))
    for col in OPTIONAL_COLUMNS:
        if col not in raw.columns:
            raw[col] = ""

    good, rejected = [], []
    for index, row in raw.iterrows():
        row_number = index + 2  # header is line 1
        try:
            try:
                date_value = dt.datetime.strptime(
                    str(row["date"]).strip(), "%Y-%m-%d").date()
            except ValueError:
                raise ValueError("date must use YYYY-MM-DD format")

            service = str(row["service"]).strip()
            item = str(row["item"]).strip()
            unit = str(row["unit"]).strip()
            if not service or not item or not unit:
                raise ValueError("service, item and unit cannot be blank")

            prepared = parse_int(row["prepared"], "prepared")
            sold = parse_int(row["sold"], "sold")
            leftover = parse_int(row["leftover"], "leftover")
            other_qty = parse_int(row["other_qty"], "other_qty", optional=True)
            unmet = parse_int(row["unmet_requests"], "unmet_requests", optional=True)
            wasted = parse_int(row["wasted"], "wasted", optional=True)
            stockout = parse_bool(row["stockout"], "stockout")

            if prepared == 0:
                raise ValueError("prepared must be greater than zero")
            other_qty = 0 if other_qty is None else other_qty

            if prepared != sold + leftover + other_qty:
                raise ValueError("quantity mismatch: prepared must equal "
                                 "sold + leftover + other_qty")
            if wasted is not None and wasted > leftover:
                raise ValueError("wasted cannot be greater than leftover")
            if stockout and leftover != 0:
                raise ValueError("stockout=true requires leftover to be zero")
            if not stockout and unmet is not None and unmet > 0:
                raise ValueError("unmet_requests cannot be greater than zero "
                                 "when stockout=false")

            good.append({
                "date": pd.Timestamp(date_value),
                "service": service, "item": item, "unit": unit,
                # lowercase keys: used for duplicate check AND grouping
                "service_key": service.lower(),
                "item_key": item.lower(),
                "unit_key": unit.lower(),
                "prepared": prepared, "sold": sold, "leftover": leftover,
                "other_qty": other_qty, "stockout": stockout,
                "unmet": unmet, "wasted": wasted,
                "stockout_time": str(row["stockout_time"]).strip(),
                "note": str(row["note"]).strip(),
                "_row_number": row_number,
            })
        except ValueError as exc:
            rejected.append(f"Row {row_number}: {exc}")

    df = pd.DataFrame(good)

    if not df.empty:
        dup = df.duplicated(subset=["date", "service_key", "item_key"], keep=False)
        for _, d in df[dup].iterrows():
            rejected.append(f"Row {d['_row_number']}: duplicate "
                            "date + service + item record")
        df = df[~dup].copy()

    if not df.empty:
        # all-blank optional columns must still be numeric
        for col in ("unmet", "wasted", "other_qty"):
            df[col] = pd.to_numeric(df[col], errors="coerce")
        # Demand: normal day = sold; stockout with unmet = sold + unmet;
        # stockout without unmet = sold is only a floor.
        df["forecast_demand"] = df["sold"] + df["unmet"].fillna(0)
        df["unknown_demand"] = df["stockout"] & df["unmet"].isna()

    return df, rejected


def recommend(group, target_day, pct, min_samples, min_weekday_rows):
    """Percentile of past demand. Unknown-demand days enter at `sold` (a floor)."""
    if group["date"].nunique() < min_samples:
        return None, "insufficient data"

    weekday_data = group[group["date"].dt.day_name() == target_day]
    weekday_dates = weekday_data["date"].nunique()

    if weekday_dates >= max(min_weekday_rows, min_samples):
        selected, basis = weekday_data, f"{weekday_dates} past {target_day}s"
    else:
        selected, basis = group, f"all {group['date'].nunique()} days"

    value = selected["forecast_demand"].quantile(pct / 100, interpolation="higher")
    return math.ceil(value), basis


def analyze(df, target_day, pct, min_samples, min_weekday_rows,
            service_filter=None):
    """Print the report. Always returns (valid_record_count, warnings)."""
    total_valid = len(df)
    warnings = []

    if service_filter:
        df = df[df["service_key"] == service_filter.strip().lower()].copy()
    if df.empty:
        print("No valid records found for this service.")
        return total_valid, warnings

    print("\nCANTEENWISE - FOOD PREPARATION REPORT")
    print("=" * WIDTH)
    print(f"Target day: {target_day}")
    print(f"Service filter: {service_filter or 'All services'}")
    print(f"Valid records in file: {total_valid} | in this report: {len(df)}")
    print(f"Demand percentile: {pct}%")
    print("\nRECOMMENDATIONS")
    print("-" * WIDTH)
    print(f"{'Item':20} {'Service':12} {'Days':>5} {'Avg Prep':>10} "
          f"{'Avg Demand Floor':>17} {'Waste %':>9} {'Stockout %':>11} "
          f"{'Recommendation':>16}")
    print("-" * WIDTH)

    for _, group in df.groupby(["item_key", "service_key", "unit_key"], sort=True):
        item = group["item"].iloc[0]
        service = group["service"].iloc[0]
        unit = group["unit"].iloc[0]
        days = group["date"].nunique()

        measured = group[group["wasted"].notna()]
        if not measured.empty:
            waste_text = f"{measured['wasted'].sum() / measured['prepared'].sum() * 100:.1f}%"
        else:
            waste_text = "N/A"

        unknown = int(group["unknown_demand"].sum())
        recorded_unmet = int(group["unmet"].fillna(0).sum())
        rec, basis = recommend(group, target_day, pct, min_samples, min_weekday_rows)
        rec_text = "Not enough data" if rec is None else f"{rec} {unit}"

        print(f"{item[:20]:20} {service[:12]:12} {days:>5} "
              f"{group['prepared'].mean():>10.1f} "
              f"{group['forecast_demand'].mean():>17.1f} "
              f"{waste_text:>9} {group['stockout'].mean() * 100:>10.1f}% "
              f"{rec_text:>16}")
        print(f"  Basis: {basis} | Waste measured: {len(measured)}/{len(group)} "
              f"| Recorded unmet: {recorded_unmet} | Unknown-demand stockout days: {unknown}")

        if unknown:
            warnings.append(f"{item} ({service}): {unknown} stockout day(s) had "
                            "unknown unmet demand; sold was used as a floor, "
                            "so the recommendation may be too low.")
        if len(measured) < len(group):
            warnings.append(f"{item} ({service}): waste recorded for only "
                            f"{len(measured)}/{len(group)} records.")

    return total_valid, warnings


def main():
    tomorrow = (dt.date.today() + dt.timedelta(days=1)).strftime("%A")
    p = argparse.ArgumentParser(
        description="CanteenWise: food preparation planner from canteen records.")
    p.add_argument("--csv", default="data.csv")
    p.add_argument("--pct", type=int, default=80,
                   help="demand percentile, 50-99 (default 80)")
    p.add_argument("--day", type=normalize_weekday, default=tomorrow,
                   help=f"weekday to plan for (default: {tomorrow})")
    p.add_argument("--service", default=None, help="e.g. lunch")
    p.add_argument("--min-weekday-rows", type=int, default=6,
                   help="same-weekday dates needed before using weekday-only "
                        "history (default 6; with fewer, p80 is just the max)")
    p.add_argument("--min-samples", type=int, default=3,
                   help="minimum distinct dates for any recommendation")
    args = p.parse_args()

    if not 50 <= args.pct <= 99:
        p.error("--pct must be between 50 and 99")
    if args.min_samples < 1:
        p.error("--min-samples must be at least 1")
    if args.min_weekday_rows < 1:
        p.error("--min-weekday-rows must be at least 1")

    try:
        df, rejected = load_data(args.csv)
        if df.empty:
            print("No valid data rows were found.")
            for m in rejected[:10]:
                print(f"- {m}")
            return 1

        total_valid, warnings = analyze(df, args.day, args.pct, args.min_samples,
                                        args.min_weekday_rows, args.service)
        if total_valid < 20:
            warnings.append(f"Only {total_valid} valid records in the file; "
                            "collect at least 20 real ones.")
        if rejected:
            warnings.append(f"{len(rejected)} row(s) rejected during validation.")

        if warnings or rejected:
            print("\nDATA WARNINGS")
            print("-" * 60)
            for w in warnings:
                print(f"- {w}")
            for m in rejected[:10]:
                print(f"- {m}")
            if len(rejected) > 10:
                print(f"- ...and {len(rejected) - 10} more rejected row(s).")

        print("\nNote: recommendations are estimates, not guarantees.")
        return 0
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
