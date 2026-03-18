# GImage ECOM Checker

Streamlit app for checking whether a qualifying ECOM image exists in GImage for each uploaded `STYLE_ID` / `COLOR_ID` pair.

## What It Does

- Accepts a CSV or Excel file with `STYLE_ID` and `COLOR_ID`
- Normalizes common header variants like `Style ID`, `Style Number`, `Color ID`, and `Colsht`
- Removes duplicate style/color pairs before making API calls
- Checks GImage in parallel using the mapped public API
- Returns `HAS_ECOM_IMAGE` as `Yes` or `No`
- Exports results to Excel
- Logs usage to a Google Sheet as part of the required runtime configuration

## Input Format

Your file must contain these columns:

- `STYLE_ID`
- `COLOR_ID`

The app also accepts common variants like `Style ID`, `Style Number`, `Color ID`, and `Colsht`.
Blank rows are ignored. Duplicate `STYLE_ID` / `COLOR_ID` pairs are removed before request fan-out.

## API Logic

The app posts to:

```text
https://gimage.guess.com/browse/api/Style/GetAllAssetsFromStyle
```

with this payload shape:

```json
{
  "StyleId": "W6GD57D0853",
  "StyleImageQf": {
    "Colors": ["G011"]
  }
}
```

`HAS_ECOM_IMAGE` is `Yes` when the response contains an `ImageTypes` item with `ImageTypeId == "ECOMM"` and at least one image for the requested color. Invalid styles, invalid colors, and request failures are all treated as `No`.

## Local Setup

1. Create and activate a virtual environment.
2. Install dependencies:

```bash
pip install -r requirements.txt
```

3. Add Streamlit secrets for usage logging.
4. Run the app:

```bash
streamlit run app.py
```

## Required Streamlit Secrets

The app requires Google Sheets logging configuration before checks can run.

```toml
[gcp_service_account]
# Google service account fields here

[usage_log]
sheet_url = "YOUR_GOOGLE_SHEET_URL"
```

If these secrets are missing or malformed, the app stops with an error before running checks.

## Verification Cases

Known examples from `API.md`:

- `W6GD57D0853` / `G011` -> `Yes`
- `W6GD57D0853` / `JBLK` -> `Yes`
- `W6GKA8D1103` / `AKRN` -> `No`
- `E6GB00Z5371` / `G61Y` -> `Yes`
- `E6GB00Z5371` / `JBLK` -> `No`

## Notes

- The app is desktop-first.
- Results export contains exactly `STYLE_ID`, `COLOR_ID`, and `HAS_ECOM_IMAGE`.
- The implementation intentionally mirrors the structure of the existing `style-checker` app.
