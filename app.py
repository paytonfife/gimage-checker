import io
import json
import time
import urllib.error
import urllib.request
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from zoneinfo import ZoneInfo

import gspread
import pandas as pd
import streamlit as st
from google.oauth2.service_account import Credentials

APP_TITLE = "GImage ECOM Checker"
API_URL = "https://gimage.guess.com/browse/api/Style/GetAllAssetsFromStyle"
VIEWER_BASE_URL = "https://gimage.guess.com/Viewer/Style"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
WORKERS = 12
TIMEOUT = 20
GSHEET_SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
LA_TZ = ZoneInfo("America/Los_Angeles")


def format_la_timestamp() -> str:
    now = datetime.now(LA_TZ)
    hour = now.strftime("%I").lstrip("0") or "0"
    return f"{now:%Y-%m-%d} {hour}:{now:%M} {now:%p}".lower()


def require_logging_config() -> str:
    try:
        service_account_info = st.secrets["gcp_service_account"]
        sheet_url = st.secrets["usage_log"]["sheet_url"]
    except Exception as exc:
        raise RuntimeError(
            "Missing required Streamlit secrets for usage logging. "
            "Add `gcp_service_account` and `usage_log.sheet_url` before running checks. "
            "Use `.streamlit/secrets.toml.example` in the repo as the template."
        ) from exc

    if not isinstance(service_account_info, Mapping) or not str(sheet_url).strip():
        raise RuntimeError(
            "Streamlit secrets are malformed. `gcp_service_account` must be a service account "
            "object and `usage_log.sheet_url` must be a non-empty string. "
            "Check `.streamlit/secrets.toml.example` for the expected shape."
        )

    return str(sheet_url)


def log_usage(pairs_checked: int, ecom_yes_count: int, elapsed: float) -> None:
    sheet_url = require_logging_config()
    creds = Credentials.from_service_account_info(
        st.secrets["gcp_service_account"],
        scopes=GSHEET_SCOPES,
    )
    gc = gspread.authorize(creds)
    sh = gc.open_by_url(sheet_url)
    ws = sh.sheet1
    ws.append_row(
        [
            format_la_timestamp(),
            pairs_checked,
            ecom_yes_count,
            pairs_checked - ecom_yes_count,
            round(elapsed, 1),
        ],
        value_input_option="USER_ENTERED",
    )


def read_uploaded_file(uploaded_file) -> pd.DataFrame:
    if uploaded_file.name.lower().endswith(".xlsx"):
        return pd.read_excel(uploaded_file)
    return pd.read_csv(uploaded_file)


def normalize_input_columns(df: pd.DataFrame) -> pd.DataFrame:
    col_map = {}
    for col in df.columns:
        normalized = col.strip().lower().replace(" ", "_")
        if normalized in ("style_id", "style_number", "style", "styleid"):
            col_map[col] = "STYLE_ID"
        elif normalized in ("color_id", "colsht", "color", "colorid"):
            col_map[col] = "COLOR_ID"

    normalized_df = df.rename(columns=col_map)
    if "STYLE_ID" not in normalized_df.columns or "COLOR_ID" not in normalized_df.columns:
        raise ValueError(
            "Couldn't find `STYLE_ID` and `COLOR_ID` columns in your file. Use headers like "
            "`STYLE_ID`, `Style ID`, `Style Number`, `COLOR_ID`, `Color ID`, or `Colsht`."
        )

    normalized_df = normalized_df[["STYLE_ID", "COLOR_ID"]].copy()
    normalized_df["STYLE_ID"] = normalized_df["STYLE_ID"].astype(str).str.strip()
    normalized_df["COLOR_ID"] = normalized_df["COLOR_ID"].astype(str).str.strip()
    normalized_df = normalized_df[
        normalized_df["STYLE_ID"].ne("") & normalized_df["COLOR_ID"].ne("")
    ].copy()
    return normalized_df


def fetch_style_assets(style_id: str, color_id: str) -> dict:
    payload = {
        "StyleId": style_id,
        "StyleImageQf": {
            "Colors": [color_id],
        },
    }
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        API_URL,
        data=body,
        headers={
            "User-Agent": USER_AGENT,
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            response_body = resp.read()
            data = json.loads(response_body.decode("utf-8"))
            return {
                "status": resp.getcode(),
                "data": data,
                "error": "",
            }
    except urllib.error.HTTPError as exc:
        error_body = b""
        try:
            error_body = exc.read()
        except Exception:
            pass
        data = None
        if error_body:
            try:
                data = json.loads(error_body.decode("utf-8"))
            except Exception:
                data = None
        return {
            "status": exc.code,
            "data": data,
            "error": f"HTTPError: {exc}",
        }
    except Exception as exc:
        return {
            "status": None,
            "data": None,
            "error": str(exc),
        }


def get_ecom_image_count(response_data: dict | None, target_color: str) -> int:
    if not response_data:
        return 0

    style = response_data.get("Style") or {}
    image_types = style.get("ImageTypes") or []
    target_color_normalized = target_color.strip().upper()

    for image_type in image_types:
        if image_type.get("ImageTypeId") != "ECOMM":
            continue

        for color_entry in image_type.get("Colors") or []:
            color_value = str(color_entry.get("Color") or "").strip().upper()
            images = color_entry.get("Images") or []
            if color_value == target_color_normalized and len(images) > 0:
                return len(images)

        if not image_type.get("Colors") and image_type.get("Images"):
            return len(image_type.get("Images") or [])

    return 0


def check_style_color(style_id: str, color_id: str) -> dict:
    response = fetch_style_assets(style_id, color_id)
    ecom_image_count = get_ecom_image_count(response["data"], color_id)
    return {
        "STYLE_ID": style_id,
        "COLOR_ID": color_id,
        "ASSET_URL": f"{VIEWER_BASE_URL}/{style_id}-{color_id}",
        "ECOM_IMAGES_AVAILABLE": ecom_image_count,
        "HAS_ECOM_IMAGE": "Yes" if ecom_image_count > 0 else "No",
    }


def build_results_table(results: list[dict]) -> pd.DataFrame:
    results_df = pd.DataFrame(
        results,
        columns=[
            "STYLE_ID",
            "COLOR_ID",
            "ASSET_URL",
            "ECOM_IMAGES_AVAILABLE",
            "HAS_ECOM_IMAGE",
        ],
    )
    if results_df.empty:
        return results_df
    return results_df.sort_values(["STYLE_ID", "COLOR_ID"]).reset_index(drop=True)


def render_results_table(results_df: pd.DataFrame) -> None:
    st.dataframe(
        results_df,
        use_container_width=True,
        hide_index=True,
        column_config={
            "ASSET_URL": st.column_config.LinkColumn(
                "ASSET_URL",
            )
        },
    )


def run_checks(df: pd.DataFrame) -> tuple[pd.DataFrame, float]:
    progress = st.progress(0, text="Checking GImage for ECOM images...")
    results = []
    completed = 0
    total = len(df)
    start = time.time()

    tasks = [(row["STYLE_ID"], row["COLOR_ID"]) for _, row in df.iterrows()]

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = {
            pool.submit(check_style_color, style_id, color_id): idx
            for idx, (style_id, color_id) in enumerate(tasks)
        }
        for future in as_completed(futures):
            results.append(future.result())
            completed += 1
            progress.progress(
                completed / total,
                text=f"Checked {completed} of {total} style-colors...",
            )

    elapsed = time.time() - start
    progress.empty()
    return build_results_table(results), elapsed


def build_excel_file(results_df: pd.DataFrame) -> io.BytesIO:
    excel_buf = io.BytesIO()
    results_df.to_excel(excel_buf, index=False, sheet_name="Results")
    excel_buf.seek(0)
    return excel_buf


def render_upload_help() -> None:
    st.markdown(
        """
        <div style="margin-bottom:1.25rem;">
            <div style="font-family:'Bebas Neue','Arial Black',Arial,sans-serif;font-size:1.1rem;
                font-weight:700;letter-spacing:2px;text-transform:uppercase;color:#000000;margin-bottom:0.4rem;">
                Upload File
            </div>
            <p style="font-family:'Inter',Arial,sans-serif;font-size:14px;font-weight:400;
                color:#777777;margin:0;line-height:1.7;">
                Your file needs two columns:
                <strong style="color:#333333;font-weight:600;">STYLE_ID</strong> and
                <strong style="color:#333333;font-weight:600;">COLOR_ID</strong>.
                Accepts
                <code style="background:#fafafa;color:#C8102E;padding:0.1em 0.45em;
                    border:1px solid #e8e8e8;font-size:0.85em;">.csv</code> or
                <code style="background:#fafafa;color:#C8102E;padding:0.1em 0.45em;
                    border:1px solid #e8e8e8;font-size:0.85em;">.xlsx</code>
                &mdash; extra columns are ignored.
            </p>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_page() -> None:
    st.set_page_config(page_title=APP_TITLE, page_icon=":frame_with_picture:", layout="wide")

    st.markdown(
        """
        <style>
        @import url('https://fonts.googleapis.com/css2?family=Bebas+Neue&family=Inter:wght@300;400;500;600&display=swap');

        :root {
            --bg:       #f5f5f5;
            --surface:  #ffffff;
            --surface2: #fafafa;
            --border:   #e8e8e8;
            --red:      #C8102E;
            --red-dim:  rgba(200,16,46,0.06);
            --text:     #333333;
            --muted:    #777777;
            --subtle:   #555555;
        }

        /* ── Global ──────────────────────────────── */
        html, body, .stApp,
        [data-testid="stAppViewContainer"],
        [data-testid="stMain"],
        section[data-testid="stSidebar"] ~ div {
            background: var(--bg) !important;
            color: var(--text) !important;
            font-family: 'Inter', Arial, sans-serif !important;
        }

        #MainMenu, footer, header { visibility: hidden; }

        .block-container {
            padding: 3.5rem 5rem 5rem !important;
            max-width: 1100px !important;
        }

        /* ── Typography ──────────────────────────── */
        h1, h2, h3 {
            font-family: 'Bebas Neue', 'Arial Black', Arial, sans-serif !important;
            font-weight: 700 !important;
            color: #000000 !important;
            letter-spacing: 2px !important;
            text-transform: uppercase !important;
        }
        p, li { color: var(--muted) !important; line-height: 1.7 !important; }
        .stButton > button p, .stButton > button span,
        .stDownloadButton > button p, .stDownloadButton > button span,
        [data-testid="stFileUploaderDropzone"] button p,
        [data-testid="stFileUploaderDropzone"] button span {
            color: #ffffff !important;
        }

        /* ── Divider ─────────────────────────────── */
        hr, [data-testid="stDivider"] hr {
            border: none !important;
            border-top: 1px solid var(--border) !important;
            margin: 2rem 0 !important;
        }

        /* ── File Uploader ───────────────────────── */
        [data-testid="stFileUploader"] {
            background: var(--surface) !important;
            border: 1px solid var(--border) !important;
            border-radius: 0 !important;
            padding: 1.5rem !important;
        }
        [data-testid="stFileUploaderDropzone"] {
            background: transparent !important;
            border: 1px dashed var(--border) !important;
            border-radius: 0 !important;
            transition: border-color 0.25s, background 0.25s !important;
        }
        [data-testid="stFileUploaderDropzone"]:hover {
            border-color: var(--red) !important;
            background: var(--red-dim) !important;
        }
        [data-testid="stFileUploaderDropzoneInstructions"] p,
        [data-testid="stFileUploaderDropzoneInstructions"] small,
        [data-testid="stFileUploaderDropzoneInstructions"] span {
            color: var(--muted) !important;
            font-family: 'Inter', Arial, sans-serif !important;
        }
        [data-testid="stFileUploaderDropzone"] button {
            background: var(--red) !important;
            color: #ffffff !important;
            border: none !important;
            border-radius: 2px !important;
            font-family: 'Inter', Arial, sans-serif !important;
            font-size: 0.7rem !important;
            font-weight: 600 !important;
            letter-spacing: 1.5px !important;
            text-transform: uppercase !important;
        }

        /* ── Buttons ─────────────────────────────── */
        .stButton > button {
            background: var(--red) !important;
            color: #ffffff !important;
            border: none !important;
            border-radius: 2px !important;
            font-family: 'Inter', Arial, sans-serif !important;
            font-size: 0.7rem !important;
            font-weight: 600 !important;
            letter-spacing: 1.5px !important;
            text-transform: uppercase !important;
            padding: 0.8rem 2.5rem !important;
            transition: background 0.2s !important;
        }
        .stButton > button:hover {
            background: #8c0b1d !important;
            color: #ffffff !important;
            border: none !important;
        }
        .stDownloadButton > button {
            background: var(--red) !important;
            color: #ffffff !important;
            border: none !important;
            border-radius: 2px !important;
            font-family: 'Inter', Arial, sans-serif !important;
            font-size: 0.7rem !important;
            font-weight: 600 !important;
            letter-spacing: 1.5px !important;
            text-transform: uppercase !important;
            padding: 0.8rem 2.5rem !important;
            transition: background 0.2s !important;
        }
        .stDownloadButton > button:hover {
            background: #8c0b1d !important;
            color: #ffffff !important;
        }

        /* ── Metrics ─────────────────────────────── */
        [data-testid="stMetric"] {
            background: var(--surface) !important;
            border: 1px solid var(--border) !important;
            border-top: 3px solid var(--red) !important;
            border-radius: 0 !important;
            padding: 1.75rem 1.5rem 1.5rem !important;
        }
        [data-testid="stMetricLabel"] > div,
        [data-testid="stMetricLabel"] label,
        [data-testid="stMetricLabel"] p {
            font-family: 'Inter', Arial, sans-serif !important;
            font-size: 0.62rem !important;
            font-weight: 600 !important;
            letter-spacing: 1.5px !important;
            text-transform: uppercase !important;
            color: #000000 !important;
        }
        [data-testid="stMetricValue"] > div {
            font-family: 'Bebas Neue', 'Arial Black', Arial, sans-serif !important;
            font-size: 3.5rem !important;
            font-weight: 700 !important;
            color: #000000 !important;
            letter-spacing: 2px !important;
            line-height: 1.1 !important;
        }

        /* ── Alerts (red left-border callout) ────── */
        [data-testid="stAlert"],
        [data-testid="stAlert"] > div {
            border-radius: 0 4px 4px 0 !important;
            border: none !important;
            border-left: 3px solid var(--red) !important;
            background: var(--surface2) !important;
            background-color: var(--surface2) !important;
            font-family: 'Inter', Arial, sans-serif !important;
            font-size: 0.875rem !important;
        }
        [data-testid="stAlert"] p {
            color: var(--subtle) !important;
        }

        /* Progress bar color handled by theme.primaryColor in config.toml */

        /* ── Caption ─────────────────────────────── */
        .stCaption, [data-testid="stCaptionContainer"] p {
            font-family: 'Inter', Arial, sans-serif !important;
            font-size: 0.7rem !important;
            letter-spacing: 0.06em !important;
            color: var(--muted) !important;
        }

        /* ── DataTable ───────────────────────────── */
        .stDataFrame {
            border: 1px solid var(--border) !important;
            border-radius: 0 !important;
            overflow: hidden !important;
        }

        /* ── Scrollbar ───────────────────────────── */
        ::-webkit-scrollbar { width: 5px; height: 5px; }
        ::-webkit-scrollbar-track { background: var(--bg); }
        ::-webkit-scrollbar-thumb { background: var(--border); border-radius: 3px; }
        ::-webkit-scrollbar-thumb:hover { background: var(--red); }

        /* ── Footer (black closing block) ────────── */
        .ai-office-branding {
            background-color: #000000;
            text-align: center;
            font-family: 'Inter', Arial, sans-serif;
            font-size: 0.65rem;
            font-weight: 600;
            letter-spacing: 1.5px;
            text-transform: uppercase;
            color: #ffffff;
            padding: 1.5rem;
            margin-top: 2rem;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    st.markdown(
        """
        <div style="display:flex;align-items:center;gap:10px;margin-bottom:0.75rem;">
            <div style="width:40px;height:2px;background:#C8102E;flex-shrink:0;"></div>
            <span style="font-family:'Inter',Arial,sans-serif;font-size:11px;font-weight:600;
                letter-spacing:4px;color:#000000;text-transform:uppercase;white-space:nowrap;">
                GUESS &mdash; ECOM
            </span>
            <div style="width:40px;height:2px;background:#C8102E;flex-shrink:0;"></div>
        </div>
        <h1 style="font-family:'Bebas Neue','Arial Black',Arial,sans-serif;font-weight:700;
            font-size:3.5rem;letter-spacing:2px;text-transform:uppercase;
            margin:0 0 0.5rem 0;color:#000000;line-height:1.05;">
            GImage Checker
        </h1>
        <p style="font-family:'Inter',Arial,sans-serif;font-size:13px;font-weight:500;
            letter-spacing:0.5px;color:#999999;text-transform:uppercase;margin:0 0 0.25rem 0;">
            Batch-verify ECOM image availability across style-color combinations.
        </p>
        """,
        unsafe_allow_html=True,
    )

    st.divider()
    render_upload_help()

    uploaded = st.file_uploader(
        "Drop your file here",
        type=["csv", "xlsx"],
        label_visibility="collapsed",
    )

    if uploaded:
        try:
            raw_df = read_uploaded_file(uploaded)
            df = normalize_input_columns(raw_df)
        except Exception as exc:
            st.error(f"Couldn't read your file: {exc}")
            st.stop()

        valid_row_count = len(df)
        df = df.drop_duplicates(["STYLE_ID", "COLOR_ID"])
        unique_pair_count = len(df)
        duplicate_count = valid_row_count - unique_pair_count

        st.success(f"**{unique_pair_count}** unique style-colors found in your file.")
        if duplicate_count:
            st.info(
                f"Removed **{duplicate_count}** duplicate row(s). "
                f"Using **{unique_pair_count}** unique style-colors from **{valid_row_count}** valid row(s)."
            )

        if st.button("Check GImage", type="primary", use_container_width=True):
            try:
                require_logging_config()
            except RuntimeError as exc:
                st.error(str(exc))
                st.stop()

            results_df, elapsed = run_checks(df)
            ecom_yes_count = int(results_df["HAS_ECOM_IMAGE"].eq("Yes").sum())

            try:
                log_usage(len(results_df), ecom_yes_count, elapsed)
            except Exception as exc:
                st.error(
                    "Usage logging failed. Results were generated but logging is required. "
                    "Make sure the Google Sheet URL is correct, the service account JSON is valid, "
                    "and the sheet is shared with the service account email. "
                    f"Original error: {exc}"
                )
                st.stop()

            st.session_state["results_df"] = results_df
            st.session_state["elapsed"] = elapsed

        if "results_df" in st.session_state:
            results_df = st.session_state["results_df"]
            elapsed = st.session_state["elapsed"]
            total = len(results_df)
            yes_count = int(results_df["HAS_ECOM_IMAGE"].eq("Yes").sum())
            no_count = total - yes_count

            st.divider()
            col1, col2, col3 = st.columns(3)
            col1.metric("Style-Colors Checked", total)
            col2.metric("ECOM Found", yes_count)
            col3.metric("No ECOM", no_count)
            st.caption(f"Completed in {elapsed:.1f}s")

            excel_buf = build_excel_file(results_df)
            st.download_button(
                label="Download Results",
                data=excel_buf,
                file_name=f"gimage_ecom_check_{time.strftime('%Y%m%d_%H%M')}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                type="primary",
                use_container_width=True,
            )

            st.divider()
            st.markdown(
                """<div style="font-family:'Bebas Neue','Arial Black',Arial,sans-serif;font-size:1.1rem;
                    font-weight:700;letter-spacing:2px;text-transform:uppercase;color:#000000;margin-bottom:0.75rem;">
                    Results
                </div>""",
                unsafe_allow_html=True,
            )
            render_results_table(results_df)

    st.markdown(
        '<div class="ai-office-branding">Built by the AI Office</div>',
        unsafe_allow_html=True,
    )


def main() -> None:
    render_page()


if __name__ == "__main__":
    main()
