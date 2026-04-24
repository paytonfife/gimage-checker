# GImage Asset Checker

Streamlit app for checking whether Ecom, Ghost, and Swatch images exist in GImage for each uploaded `STYLE_ID` / `COLOR_ID` pair, split by NA and EU region.

## What It Does

- Accepts a CSV or Excel file with `STYLE_ID` and `COLOR_ID`
- Normalizes common header variants like `Style ID`, `Style Number`, `Color ID`, and `Colsht`
- Removes duplicate style-colors before making API calls
- Checks GImage in parallel using the mapped public API
- Returns an `Images` link in the format `https://gimage.guess.com/Viewer/Style/STYLE-COLOR`
- Returns separate `Yes` / `No` columns for `NA Ecom`, `NA Ghost`, `NA Swatch`, `EU Ecom`, `EU Ghost`, and `EU Swatch`
- Returns a `Missing Images` column with a copy-friendly list of missing region/type combinations
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

Availability is derived from image entries for the requested color, grouped by `RegionId`.

| Result label | GImage `ImageTypeId` |
|---|---|
| Ecom | `ECOMM` |
| Ghost | `GHOST` |
| Swatch | `SW` |

A row can be `Yes` for both regions when the same style-color asset exists in both folders. Invalid styles, invalid colors, and request failures are treated as `No` for all region/type checks.

The `Images` link is constructed as:

```text
https://gimage.guess.com/Viewer/Style/<STYLE_ID>-<COLOR_ID>
```

The on-screen results table and Excel export use readable headers such as `NA Ecom`, `NA Ghost`, `EU Swatch`, and `Missing Images`.

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

Quick syntax check:

```bash
python -m py_compile app.py
```

Repo:
`https://github.com/paytonfife/gimage-checker`

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
   - `Style-Colors Checked`
   - `Complete Rows`
   - `Rows With Missing Images`
   - `Elapsed Seconds`
3. In Google Cloud, create a service account for this app and generate a JSON key.
4. Enable the Google Sheets API for that Google Cloud project.
5. Share the Google Sheet with the service account `client_email` from the JSON key and give it edit access.
6. Put the JSON fields and sheet URL into Streamlit secrets locally and in Streamlit Community Cloud.

If logging fails after deployment, the most common issue is that the sheet was not shared with the service account email.

## Streamlit Deployment

This project is intended to run on Streamlit Community Cloud from:
`https://github.com/paytonfife/gimage-checker`

Use these deploy settings:

- Branch: `main`
- Main file path: `app.py`

In Streamlit Community Cloud, add the same secrets shown above under:
`Manage app` -> `Settings` -> `Secrets`

The deployed app will not run checks unless those secrets are present and valid.

## Pushing Updates Live

The live app is connected to the GitHub repo on `main`. Updating the live app is:

1. Make changes locally.
2. Run a quick check:

```bash
python -m py_compile app.py
```

3. Review changes:

```bash
git status
git diff
```

4. Commit and push:

```bash
git add app.py README.md requirements.txt
git commit -m "short description of change"
git push origin main
```

If your change touches other files, include them in `git add` as needed.

After `git push origin main`, Streamlit Community Cloud should automatically redeploy the app. If it does not, open the app dashboard and trigger a manual reboot/redeploy.

Do not commit `.streamlit/secrets.toml`; keep secrets only in your local secrets file and in Streamlit Cloud.

## Verification Cases

Known regional examples:

- `E6GB00Z5371` / `G61Y` -> `NA Ecom=Yes`, `EU Ecom=Yes`, `EU Ghost=Yes`, `EU Swatch=Yes`
- `E6GB00Z5371` / `JBLK` -> `NA Ecom=Yes`, `NA Swatch=Yes`, `EU Ghost=Yes`, `EU Swatch=Yes`
- `W6GD57D0853` / `G011` -> `NA Ecom=Yes`, `NA Swatch=Yes`
- `W6GKA8D1103` / `AKRN` -> `NA Ecom=Yes`, `NA Swatch=Yes`, `EU Swatch=Yes`

## Notes

- The app is desktop-first.
- Results export contains `Style ID`, `Color ID`, `Images`, the six readable image availability columns, and `Missing Images`.
- The implementation intentionally mirrors the structure of the existing `style-checker` app.
- Local sample file `test_style_colors.csv` can be used for smoke testing, but it is intentionally not tracked in Git unless you choose to add it.
