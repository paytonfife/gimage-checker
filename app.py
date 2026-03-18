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
    st.markdown("#### Upload your file")
    st.markdown(
        "Your file needs two columns: **STYLE_ID** and **COLOR_ID**.  \n"
        "Accepts `.csv` or `.xlsx` and ignores any extra columns."
    )


def render_page() -> None:
    st.set_page_config(page_title=APP_TITLE, page_icon=":frame_with_picture:", layout="wide")

    st.markdown(
        """
        <style>
        #MainMenu, footer, header {visibility: hidden;}
        .block-container {padding: 2rem 3rem; max-width: 1280px;}
        [data-testid="stFileUploader"] {
            border: 2px dashed #d0d0d0;
            border-radius: 12px;
            padding: 1rem;
            background: #fafafa;
        }
        .stDataFrame {border-radius: 8px; overflow: hidden;}
        .ai-office-branding {
            text-align: center;
            color: #9ca3af;
            font-size: 0.75em;
            padding: 2rem 0 1rem 0;
            letter-spacing: 0.03em;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    st.markdown(f"## {APP_TITLE}")
    st.markdown(
        "Upload a list of style-colors to check whether a qualifying "
        "ECOM image exists in GImage."
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
            st.markdown("#### Results")
            render_results_table(results_df)

    st.markdown(
        '<div class="ai-office-branding">Built by the AI Office</div>',
        unsafe_allow_html=True,
    )


def main() -> None:
    render_page()


if __name__ == "__main__":
    main()
