# GImage ECOM Checker

Streamlit app that takes a style/color list and returns whether an ECOM image exists in GImage for each style/color combination.

## Background

The Ecom team (Aurelie Benarouche) currently checks styles one-by-one in GImage to see if an ECOM image is available before pushing products live. Ghost and sketch images can't be used — only ECOM images qualify. This app automates that batch check.

## Project tracking

Updates, status, and context live in the small projects log:
`pf2/context/project-context/small-projects.md` → **GImage ECOM Checker**

## When building

Mirror the style-checker app in structure and functionality:
- Same input format: CSV or Excel with STYLE_ID and COLOR_ID columns
- Same Streamlit UI pattern: file upload, run button, results table, download as Excel
- Same parallel fetch approach (ThreadPoolExecutor)
- Output: HAS_ECOM_IMAGE yes/no per style/color pair

Style-checker repo for reference: `C:\Users\pfife\OneDrive - Guess Inc\Desktop\style-checker`

## API

The GImage API has been mapped. Full details in [API.md](API.md).

**Summary:** POST to `https://gimage.guess.com/browse/api/Style/GetAllAssetsFromStyle` with `{"StyleId": "...", "StyleImageQf": {"Colors": ["..."]}}`. No auth required. Check response for `ImageTypeId === "ECOMM"` with a non-empty `Images` array.
