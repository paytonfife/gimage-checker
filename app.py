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
            <div style="font-family:'DM Sans',sans-serif;font-size:0.62rem;font-weight:500;
                letter-spacing:0.22em;text-transform:uppercase;color:#c01028;margin-bottom:0.5rem;">
                Upload File
            </div>
            <p style="font-family:'DM Sans',sans-serif;font-weight:300;font-size:0.875rem;
                color:#8a8480;margin:0;line-height:1.65;">
                Your file needs two columns:
                <strong style="color:#1a1616;font-weight:500;">STYLE_ID</strong> and
                <strong style="color:#1a1616;font-weight:500;">COLOR_ID</strong>.
                Accepts
                <code style="background:#ede9e4;color:#c01028;padding:0.1em 0.45em;
                    border-radius:2px;font-size:0.85em;">.csv</code> or
                <code style="background:#ede9e4;color:#c01028;padding:0.1em 0.45em;
                    border-radius:2px;font-size:0.85em;">.xlsx</code>
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
        @import url('https://fonts.googleapis.com/css2?family=Cormorant+Garamond:ital,wght@0,300;0,400;0,600;1,300&family=DM+Sans:opsz,wght@9..40,300;9..40,400;9..40,500&display=swap');

        :root {
            --bg:       #f5f2ee;
            --surface:  #ffffff;
            --surface2: #ede9e4;
            --border:   #dbd5cf;
            --gold:     #c01028;
            --gold-dim: rgba(192,16,40,0.06);
            --text:     #1a1616;
            --muted:    #8a8480;
            --green:    #2d6a42;
            --red:      #c01028;
        }

        /* ── Global ──────────────────────────────── */
        html, body, .stApp,
        [data-testid="stAppViewContainer"],
        [data-testid="stMain"],
        section[data-testid="stSidebar"] ~ div {
            background: var(--bg) !important;
            color: var(--text) !important;
            font-family: 'DM Sans', sans-serif !important;
        }

        #MainMenu, footer, header { visibility: hidden; }

        .block-container {
            padding: 3.5rem 5rem 5rem !important;
            max-width: 1100px !important;
        }

        /* ── Typography ──────────────────────────── */
        h1, h2, h3 {
            font-family: 'Cormorant Garamond', serif !important;
            font-weight: 300 !important;
            color: var(--text) !important;
            letter-spacing: 0.05em !important;
        }
        p, li { color: var(--muted) !important; line-height: 1.65 !important; }

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
            border-radius: 3px !important;
            padding: 1.5rem !important;
        }
        [data-testid="stFileUploaderDropzone"] {
            background: transparent !important;
            border: 1px dashed var(--border) !important;
            border-radius: 3px !important;
            transition: border-color 0.25s, background 0.25s !important;
        }
        [data-testid="stFileUploaderDropzone"]:hover {
            border-color: var(--gold) !important;
            background: var(--gold-dim) !important;
        }
        [data-testid="stFileUploaderDropzoneInstructions"] p,
        [data-testid="stFileUploaderDropzoneInstructions"] small,
        [data-testid="stFileUploaderDropzoneInstructions"] span {
            color: var(--muted) !important;
            font-family: 'DM Sans', sans-serif !important;
        }
        [data-testid="stFileUploaderDropzone"] button {
            background: var(--gold) !important;
            color: #ffffff !important;
            border: none !important;
            border-radius: 2px !important;
            font-family: 'DM Sans', sans-serif !important;
            font-size: 0.7rem !important;
            font-weight: 500 !important;
            letter-spacing: 0.15em !important;
            text-transform: uppercase !important;
        }

        /* ── Buttons ─────────────────────────────── */
        .stButton > button {
            background: var(--gold) !important;
            color: #ffffff !important;
            border: none !important;
            border-radius: 2px !important;
            font-family: 'DM Sans', sans-serif !important;
            font-size: 0.7rem !important;
            font-weight: 500 !important;
            letter-spacing: 0.22em !important;
            text-transform: uppercase !important;
            padding: 0.8rem 2.5rem !important;
            transition: background 0.2s, color 0.2s !important;
        }
        .stButton > button:hover {
            background: #8c0b1d !important;
            color: #ffffff !important;
            border: none !important;
        }
        .stDownloadButton > button {
            background: var(--gold) !important;
            color: #ffffff !important;
            border: none !important;
            border-radius: 2px !important;
            font-family: 'DM Sans', sans-serif !important;
            font-size: 0.7rem !important;
            font-weight: 500 !important;
            letter-spacing: 0.22em !important;
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
            border-top: 2px solid var(--gold) !important;
            border-radius: 3px !important;
            padding: 1.75rem 1.5rem 1.5rem !important;
        }
        [data-testid="stMetricLabel"] > div,
        [data-testid="stMetricLabel"] label,
        [data-testid="stMetricLabel"] p {
            font-family: 'DM Sans', sans-serif !important;
            font-size: 0.62rem !important;
            font-weight: 500 !important;
            letter-spacing: 0.2em !important;
            text-transform: uppercase !important;
            color: var(--muted) !important;
        }
        [data-testid="stMetricValue"] > div {
            font-family: 'Cormorant Garamond', serif !important;
            font-size: 3rem !important;
            font-weight: 300 !important;
            color: var(--text) !important;
            line-height: 1.1 !important;
        }

        /* ── Alerts ──────────────────────────────── */
        [data-testid="stAlert"] {
            border-radius: 3px !important;
            border: none !important;
            border-left: 2px solid var(--border) !important;
            background: var(--surface) !important;
            font-family: 'DM Sans', sans-serif !important;
            font-size: 0.875rem !important;
            color: var(--muted) !important;
        }
        [data-testid="stAlert"] p,
        [data-testid="stAlert"] div {
            color: var(--muted) !important;
        }

        /* ── Progress ────────────────────────────── */
        [data-testid="stProgress"] > div {
            background: var(--border) !important;
            border-radius: 2px !important;
            height: 3px !important;
        }
        [data-testid="stProgress"] > div > div {
            background: var(--gold) !important;
            border-radius: 2px !important;
        }

        /* ── Caption ─────────────────────────────── */
        .stCaption, [data-testid="stCaptionContainer"] p {
            font-family: 'DM Sans', sans-serif !important;
            font-size: 0.7rem !important;
            letter-spacing: 0.06em !important;
            color: var(--muted) !important;
        }

        /* ── DataTable ───────────────────────────── */
        .stDataFrame {
            border: 1px solid var(--border) !important;
            border-radius: 3px !important;
            overflow: hidden !important;
        }

        /* ── Scrollbar ───────────────────────────── */
        ::-webkit-scrollbar { width: 5px; height: 5px; }
        ::-webkit-scrollbar-track { background: var(--surface2); }
        ::-webkit-scrollbar-thumb { background: var(--border); border-radius: 3px; }
        ::-webkit-scrollbar-thumb:hover { background: var(--red); }

        /* ── Footer ──────────────────────────────── */
        .ai-office-branding {
            text-align: center;
            font-family: 'DM Sans', sans-serif;
            font-size: 0.6rem;
            letter-spacing: 0.2em;
            text-transform: uppercase;
            color: #8a8480;
            padding: 3rem 0 1rem 0;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    st.markdown(
        """
        <div style="margin-bottom:0.2rem;">
            <span style="font-family:'DM Sans',sans-serif;font-size:0.62rem;font-weight:500;
                letter-spacing:0.28em;text-transform:uppercase;color:#c01028;">
                GUESS &mdash; ECOM
            </span>
        </div>
        <h1 style="font-family:'Cormorant Garamond',serif;font-weight:300;font-size:3.25rem;
            letter-spacing:0.04em;margin:0.15rem 0 0.6rem 0;color:#1a1616;line-height:1.1;">
            GImage Checker
        </h1>
        <p style="font-family:'DM Sans',sans-serif;font-weight:300;font-size:0.875rem;
            color:#8a8480;margin:0 0 0.25rem 0;letter-spacing:0.015em;line-height:1.65;">
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
                """<div style="font-family:'DM Sans',sans-serif;font-size:0.62rem;font-weight:500;
                    letter-spacing:0.22em;text-transform:uppercase;color:#c01028;margin-bottom:0.75rem;">
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
