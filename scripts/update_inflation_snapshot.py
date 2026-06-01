#!/usr/bin/env python3
import json
from datetime import datetime, timezone
from pathlib import Path

import requests

BLS_API_BASE = "https://api.bls.gov/publicAPI/v2/timeseries/data/"
BLS_CPI_RELEASE_URL = "https://www.bls.gov/news.release/cpi.htm"
BLS_CPI_SCHEDULE_URL = "https://www.bls.gov/schedule/news_release/cpi.htm"

# CPI-U all items
SERIES_SA = "CUSR0000SA0"   # seasonally adjusted
SERIES_NSA = "CUUR0000SA0"  # not seasonally adjusted

ROOT = Path(__file__).resolve().parents[1]
OUTPUT_PATH = ROOT / "data" / "inflation_snapshot.json"


def fetch_series(series_id: str) -> list[dict]:
    response = requests.get(f"{BLS_API_BASE}{series_id}", timeout=30)
    response.raise_for_status()

    payload = response.json()
    if payload.get("status") != "REQUEST_SUCCEEDED":
        raise RuntimeError(f"BLS API request failed for {series_id}: {payload}")

    rows = payload["Results"]["series"][0]["data"]
    monthly_rows = [
        row for row in rows
        if row.get("period", "").startswith("M") and row["period"] != "M13"
    ]
    return monthly_rows


def month_index(year: int, period: str) -> int:
    return year * 12 + int(period[1:])


def find_previous_month(data: list[dict], current: dict) -> dict:
    current_idx = month_index(int(current["year"]), current["period"])
    for row in data:
        if month_index(int(row["year"]), row["period"]) == current_idx - 1:
            return row
    raise RuntimeError("Previous month not found in BLS data.")


def find_same_month_previous_year(data: list[dict], current: dict) -> dict:
    target_year = int(current["year"]) - 1
    target_period = current["period"]

    for row in data:
        if int(row["year"]) == target_year and row["period"] == target_period:
            return row

    raise RuntimeError("Same month previous year not found in BLS data.")


def percent_change(new: float, old: float) -> float:
    return ((new / old) - 1.0) * 100.0


def build_editorial_summary(reference_month: str, monthly_pct: float, annual_pct: float, prev_annual_pct: float) -> str:
    if annual_pct > prev_annual_pct:
        direction = "above"
    elif annual_pct < prev_annual_pct:
        direction = "below"
    else:
        direction = "in line with"

    return (
        f"The latest CPI snapshot for {reference_month} shows a {monthly_pct:.1f}% month-over-month change "
        f"and a {annual_pct:.1f}% 12-month change. The annual reading is {direction} the prior release, "
        f"so this can serve as a practical baseline scenario in the calculator while you also test lower and higher assumptions."
    )


def main():
    sa_series = fetch_series(SERIES_SA)
    nsa_series = fetch_series(SERIES_NSA)

    current_sa = sa_series[0]
    previous_sa = find_previous_month(sa_series, current_sa)

    current_nsa = nsa_series[0]
    previous_year_nsa = find_same_month_previous_year(nsa_series, current_nsa)

    previous_month_nsa = find_previous_month(nsa_series, current_nsa)
    previous_month_previous_year_nsa = find_same_month_previous_year(nsa_series, previous_month_nsa)

    monthly_pct = percent_change(float(current_sa["value"]), float(previous_sa["value"]))
    annual_pct = percent_change(float(current_nsa["value"]), float(previous_year_nsa["value"]))
    previous_annual_pct = percent_change(
        float(previous_month_nsa["value"]),
        float(previous_month_previous_year_nsa["value"])
    )

    reference_month = f'{current_nsa["periodName"]} {current_nsa["year"]}'
    editorial_summary = build_editorial_summary(
        reference_month=reference_month,
        monthly_pct=monthly_pct,
        annual_pct=annual_pct,
        prev_annual_pct=previous_annual_pct
    )

    snapshot = {
        "reference_month": reference_month,
        "monthly_cpi_change": f"{monthly_pct:.1f}%",
        "annual_cpi_change": f"{annual_pct:.1f}%",
        "previous_annual_cpi_change": f"{previous_annual_pct:.1f}%",
        "next_release_reference_month": "See BLS schedule",
        "next_release_date": "Check BLS CPI release calendar",
        "next_release_time": "08:30 AM ET",
        "editorial_summary": editorial_summary,
        "source_name": "U.S. Bureau of Labor Statistics",
        "source_release_url": BLS_CPI_RELEASE_URL,
        "source_schedule_url": BLS_CPI_SCHEDULE_URL,
        "series_ids": {
            "monthly_seasonally_adjusted_all_items": SERIES_SA,
            "annual_not_seasonally_adjusted_all_items": SERIES_NSA
        },
        "updated_at_utc": datetime.now(timezone.utc).isoformat()
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(snapshot, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
