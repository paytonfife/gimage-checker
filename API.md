# GImage API Reference

## Endpoint

```
POST https://gimage.guess.com/browse/api/Style/GetAllAssetsFromStyle
```

No authentication required — the API is public.

## Request

**Headers:**
```
Content-Type: application/json
```

**Body:**
```json
{
  "StyleId": "W6GD57D0853",
  "StyleImageQf": {
    "Colors": ["G011"]
  }
}
```

- `Colors`: array of color codes to filter by. Pass `[]` to return all colors for the style.

## Response Shape

```json
{
  "Style": {
    "Id": "W6GD57D0853",
    "ImageTypes": [
      {
        "ImageTypeId": "ECOMM",
        "ImageTypeDs": "E-Commerce",
        "Colors": [
          {
            "Color": "G011",
            "Images": [ ... ],
            "MissingImages": []
          }
        ]
      },
      ...
    ]
  }
}
```

**Known `ImageTypeId` values:** `ECOMM`, `GHOST`, `SKETCH`, `SW` (Swatch), `WORN`, `3DSKETCH`, `3D360`, `DETSTILL`

## ECOM Detection Logic

A style/color pair **has an ECOM image** if:

1. `data.Style.ImageTypes` contains an entry where `ImageTypeId === "ECOMM"`
2. AND that entry's `Colors` array contains the target color with `Images.length > 0`

When passing a specific color in `StyleImageQf.Colors`, the response will only include that color — so the check simplifies to: does any `ImageTypes` entry have `ImageTypeId === "ECOMM"` with a non-empty `Images` array?

## Validated Test Cases

| Style | Color | Expected | Result |
|---|---|---|---|
| `W6GD57D0853` | G011 | Has ECOMM | ✅ ECOMM — 5 images |
| `W6GD57D0853` | JBLK | Has ECOMM | ✅ ECOMM — 5 images |
| `W6GKA8D1103` | AKRN | No ECOMM | ✅ No ECOMM (WORN, SKETCH, etc.) |
| `E6GB00Z5371` | G61Y | Has ECOMM | ✅ ECOMM — 5 images |
| `E6GB00Z5371` | JBLK | No ECOMM | ✅ No ECOMM (GHOST, SKETCH only) |

## Source

Endpoint and request shape discovered by inspecting the JS bundle at:
```
https://gimage.guess.com/browse/static/js/main.34436b26.js
```

API base URL defined in:
```
https://gimage.guess.com/browse/config.js → apiNamespace: "/browse/api/"
```
