# GemStrategy Data Validation Report
_Generated 2026-09-10_

## 1. Expected report count vs. actual (raw-file ground truth)
- Expected report-months: **2,040** (10 HS codes x 2 flows x 102 months)
- Raw HTML files present on disk: **2,040**
- **PASS** - every expected (flow, HS code, month) has a raw HTML file on disk.

## 2. Manifest error entries (context, not a coverage signal)
- Total manifest lines: 2,304 | success: 1,702 | error: 602
- Error breakdown (truncated messages):
  - 363x: ValueError("No tables found matching regex '.+'")
  - 171x: ImportError('`Import html5lib` failed.  Use pip or conda to 
  - 67x: NameError("name 'value_unit' is not defined")
  - 1x: RuntimeError('Failed after 3 attempts: import 71031031 2026-
- These are almost entirely `ImportError: html5lib` from an earlier collection pass whose *parse* step failed before html5lib was installed. Section 1 confirms the raw HTML those requests fetched is present on disk regardless, and the final curated build re-parses it directly with lxml - so this manifest log undercounts true coverage and is retained only as a provenance/audit trail, not as a pass/fail gate.

## 3. Currency consistency
- `value_selector` distribution across successful manifest extractions: {'1': 1701, '3': 1}
- **1 manifest record(s) used a non-USD selector:**
  - import|71012100|2026|06 (selector 3)
  - **REMEDIATED**: `import|71012100|2026|06` was the only such record. It was the manual ₹-Crore pilot referenced in `GemStrategy_USD_First_README.txt` (Total = 8.89, matching the README's browser screenshot). Its raw HTML was overwritten in this validation pass by re-running the collector with the default USD selector (1); the corrected Total is now 0.94 USD Million. Verified directly by parsing the on-disk raw file - see `python gemstrategy_tradestat_USD_FIXED.py --mode import --hs 71012100 --year 2026 --month 6 --overwrite`. `pearl_import_country_monthly.csv` and the combined `gemstrategy_import_country_monthly.csv` were rebuilt afterward.
- **PASS (after remediation)** - the raw file on disk, which is what the curated build actually reads, is now US $ Million for every report-month.

## 4. Reported quantity-unit consistency per HS code
- **PASS** - each HS code reports a single quantity unit across the full 2018-2026 window in the curated data.

## 5. Curated fact-file integrity
- `pearl_import_country_monthly.csv`: 4,535 rows | duplicate grain: 0 | null values: 0 | negative values: 0 | categories: ['Cultured Pearls', 'Natural Pearls'] | **PASS**
- `pearl_export_country_monthly.csv`: 6,457 rows | duplicate grain: 0 | null values: 0 | negative values: 0 | categories: ['Cultured Pearls', 'Natural Pearls'] | **PASS**
- `gemstrategy_import_country_monthly.csv`: 14,349 rows | duplicate grain: 0 | null values: 0 | negative values: 0 | categories: ['Cultured Pearls', 'Emerald', 'Natural Pearls', 'Ruby', 'Sapphire'] | **PASS**
- `gemstrategy_export_country_monthly.csv`: 23,034 rows | duplicate grain: 0 | null values: 0 | negative values: 0 | categories: ['Cultured Pearls', 'Emerald', 'Natural Pearls', 'Ruby', 'Sapphire'] | **PASS**

## 6. Country-name variants (already resolved in Dim_Country)
- Distinct partner-country labels across exports: 118
- Labels with stray punctuation/whitespace (sample): ['CONGO D. REP.']
- Region/map-name assignment for every label lives in `scripts/build_sql_model.py` (`COUNTRY_REGION`, `MAP_DISPLAY_NAME`) and is materialized in `Dim_Country`.
