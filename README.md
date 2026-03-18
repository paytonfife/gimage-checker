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

Copy `.streamlit/secrets.toml.example` to `.streamlit/secrets.toml` locally, then replace the placeholder values.

```toml
[gcp_service_account]
type = "service_account"
project_id = "your-gcp-project-id"
private_key_id = "your-private-key-id"
private_key = "-----BEGIN PRIVATE KEY-----\nYOUR_PRIVATE_KEY_HERE\n-----END PRIVATE KEY-----\n"
client_email = "gimage-checker@your-gcp-project-id.iam.gserviceaccount.com"
client_id = "your-client-id"
auth_uri = "https://accounts.google.com/o/oauth2/auth"
token_uri = "https://oauth2.googleapis.com/token"
auth_provider_x509_cert_url = "https://www.googleapis.com/oauth2/v1/certs"
client_x509_cert_url = "https://www.googleapis.com/robot/v1/metadata/x509/gimage-checker%40your-gcp-project-id.iam.gserviceaccount.com"
universe_domain = "googleapis.com"

[usage_log]
sheet_url = "https://docs.google.com/spreadsheets/d/YOUR_SHEET_ID/edit#gid=0"
```

If these secrets are missing or malformed, the app stops with an error before running checks.

## Google Sheet Setup

1. Create a Google Sheet that will receive usage logs.
2. Add a header row with:
   - `Timestamp`
   - `Pairs Checked`
   - `ECOM Yes`
   - `ECOM No`
   - `Elapsed Seconds`
3. In Google Cloud, create a service account for this app and generate a JSON key.
4. Enable the Google Sheets API for that Google Cloud project.
5. Share the Google Sheet with the service account `client_email` from the JSON key and give it edit access.
6. Put the JSON fields and sheet URL into Streamlit secrets locally and in Streamlit Community Cloud.

If logging fails after deployment, the most common issue is that the sheet was not shared with the service account email.

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
