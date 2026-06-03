import csv
import io
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen


CSV_URL = "https://www.freddiemac.com/pmms/docs/PMMS_history.csv"
OUTPUT_PATH = Path("data/mortgage_snapshot.json")


def fetch_csv_text(url: str) -> str:
    req = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; CalcNestMortgageSnapshot/1.0)"
        },
    )
    with urlopen(req, timeout=30) as response:
        return response.read().decode("utf-8", errors="replace")


def parse_date(value: str) -> datetime:
    return datetime.strptime(value.strip(), "%m/%d/%Y")


def format_display_date(dt: datetime) -> str:
    return f"{dt.strftime('%B')} {dt.day}, {dt.year}"


def fmt_rate(value: float) -> str:
    return f"{value:.2f}%"


def fmt_change(delta: float) -> str:
    sign = "+" if delta > 0 else ""
    return f"{sign}{delta:.2f} pts vs previous week"


def build_editorial_summary(rate30: float, rate15: float, change30: float, change15: float) -> str:
    abs30 = abs(change30)
    abs15 = abs(change15)

    if abs30 < 0.05 and abs15 < 0.05:
        movement = "Weekly movement was modest versus the prior survey"
    elif change30 > 0 and change15 > 0:
        movement = "Both major fixed-rate averages moved higher versus the prior survey"
    elif change30 < 0 and change15 < 0:
        movement = "Both major fixed-rate averages moved lower versus the prior survey"
    else:
        movement = "Weekly movement was mixed across major fixed-rate products"

    return (
        f"Freddie Mac's latest weekly survey shows 30-year fixed mortgage rates at {rate30:.2f}% "
        f"and 15-year fixed mortgage rates at {rate15:.2f}%. {movement}, but even small rate "
        f"changes can still affect monthly payment estimates on larger loan balances."
    )


def load_rows(csv_text: str):
    reader = csv.DictReader(io.StringIO(csv_text))
    rows = []

    for row in reader:
        date_raw = (row.get("date") or "").strip()
        rate30_raw = (row.get("pmms30") or "").strip()
        rate15_raw = (row.get("pmms15") or "").strip()

        if not date_raw or not rate30_raw or not rate15_raw:
            continue

        try:
            row_date = parse_date(date_raw)
            rate30 = float(rate30_raw)
            rate15 = float(rate15_raw)
        except ValueError:
            continue

        rows.append(
            {
                "date": row_date,
                "rate30": rate30,
                "rate15": rate15,
            }
        )

    rows.sort(key=lambda x: x["date"])
    return rows


def main():
    csv_text = fetch_csv_text(CSV_URL)
    rows = load_rows(csv_text)

    if len(rows) < 2:
        raise RuntimeError("Not enough PMMS rows found to build mortgage snapshot.")

    latest = rows[-1]
    previous = rows[-2]

    change30 = latest["rate30"] - previous["rate30"]
    change15 = latest["rate15"] - previous["rate15"]

    payload = {
        "source": "Freddie Mac PMMS",
        "source_url": "https://www.freddiemac.com/pmms/pmms_archives",
        "source_csv_url": CSV_URL,
        "reference_date": format_display_date(latest["date"]),
        "rate_30y_fixed": fmt_rate(latest["rate30"]),
        "rate_15y_fixed": fmt_rate(latest["rate15"]),
        "weekly_change_30y": fmt_change(change30),
        "weekly_change_15y": fmt_change(change15),
        "source_cadence": "Weekly",
        "editorial_summary": build_editorial_summary(
            latest["rate30"], latest["rate15"], change30, change15
        ),
        "updated_at_utc": datetime.now(timezone.utc).isoformat(),
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    if OUTPUT_PATH.exists():
        try:
            existing = json.loads(OUTPUT_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            existing = None

        if existing:
            same_core_data = (
                existing.get("reference_date") == payload["reference_date"]
                and existing.get("rate_30y_fixed") == payload["rate_30y_fixed"]
                and existing.get("rate_15y_fixed") == payload["rate_15y_fixed"]
                and existing.get("weekly_change_30y") == payload["weekly_change_30y"]
                and existing.get("weekly_change_15y") == payload["weekly_change_15y"]
            )

            if same_core_data:
                print("No new mortgage snapshot data. Existing file unchanged.")
                sys.exit(0)

    OUTPUT_PATH.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"Mortgage snapshot updated: {payload['reference_date']}")


if __name__ == "__main__":
    main()
