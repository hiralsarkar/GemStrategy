"""Create curated gemstone and all-product TRADESTAT fact files from raw reports."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from build_pearl_trade_csvs import (
    DATA,
    HS_MASTER,
    OUTPUT_COLUMNS,
    numeric_value,
    parse_country_table,
    value_column,
)
from gemstrategy_tradestat_USD_FIXED import extract_metadata


ROOT = Path(__file__).resolve().parent.parent
GEMSTONE_FAMILIES = {"Emerald", "Ruby", "Sapphire"}


def build_flow(flow: str) -> pd.DataFrame:
    master = HS_MASTER.set_index("hs_code")
    records: list[pd.DataFrame] = []
    for raw_path in sorted((DATA / "raw" / flow).glob("*/*.html")):
        parts = raw_path.stem.split("_")
        if len(parts) != 3:
            continue
        hs_code, year_text, month_text = parts
        if hs_code not in master.index or master.loc[hs_code, "category"] not in GEMSTONE_FAMILIES:
            continue
        year, month = int(year_text), int(month_text)
        html = raw_path.read_text(encoding="utf-8")
        try:
            table = parse_country_table(html)
        except ValueError as exc:
            if "No tables found" in str(exc):
                # A source response with no country table is not a zero-value trade row.
                continue
            raise
        metadata = extract_metadata(html)
        selected_column = value_column(table.columns, year, month)
        country_column = next(
            (column for column in table.columns if str(column).strip().casefold() == "country"),
            None,
        )
        if country_column is None:
            raise ValueError(f"Country column missing from {raw_path}")

        countries = table[country_column].astype("string").str.strip()
        product = master.loc[hs_code]
        status = pd.Series(selected_column).str.extract(r"\(([RFP])\)", expand=False).iloc[0]
        report_date = pd.to_datetime(metadata.get("report_date_text"), dayfirst=True, errors="coerce")
        records.append(pd.DataFrame({
            "trade_flow": flow,
            "period_date": pd.Timestamp(year=year, month=month, day=1).date().isoformat(),
            "year": year,
            "month_number": month,
            "month_name": pd.Timestamp(year=year, month=month, day=1).strftime("%B"),
            "hs_code": hs_code,
            "product_category": product["category"],
            "product_description": product["description"],
            "partner_country": countries,
            "record_type": countries.str.casefold().eq("total").map({True: "TOTAL", False: "PARTNER"}),
            "trade_value_usd_million": numeric_value(table[selected_column]),
            "observation_status": status if pd.notna(status) else pd.NA,
            "reported_quantity_unit": metadata.get("reported_unit"),
            "source_report_date": report_date.date().isoformat() if pd.notna(report_date) else pd.NA,
            "source_value_column": selected_column,
            "source_file": raw_path.relative_to(ROOT).as_posix(),
        })[OUTPUT_COLUMNS])

    result = pd.concat(records, ignore_index=True).sort_values(
        ["period_date", "hs_code", "record_type", "partner_country"], kind="stable"
    ).reset_index(drop=True)
    grain = ["trade_flow", "period_date", "hs_code", "partner_country"]
    if result.duplicated(grain).any() or result["trade_value_usd_million"].isna().any():
        raise ValueError(f"Invalid fact grain or trade value in {flow}.")
    return result


def main() -> None:
    output_dir = DATA / "curated"
    output_dir.mkdir(parents=True, exist_ok=True)
    for flow in ("import", "export"):
        gemstones = build_flow(flow)
        pearl_path = output_dir / f"pearl_{flow}_country_monthly.csv"
        pearls = pd.read_csv(pearl_path, dtype={"hs_code": "string"})
        all_products = pd.concat([pearls, gemstones], ignore_index=True).sort_values(
            ["period_date", "hs_code", "record_type", "partner_country"], kind="stable"
        )
        all_products.to_csv(output_dir / f"gemstrategy_{flow}_country_monthly.csv", index=False, encoding="utf-8-sig")
        # Emerald/Ruby/Sapphire is separable via Dim_HS_Code[commodity_name] in
        # Power BI, so no separate gemstone_{flow} CSV is written here.
        print(f"gemstrategy_{flow}: {len(all_products):,} rows")


if __name__ == "__main__":
    main()
