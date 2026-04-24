# GImage API Reference

## Endpoint

```text
POST https://gimage.guess.com/browse/api/Style/GetAllAssetsFromStyle
```

No authentication required. The API is public.

## Request

**Headers:**

```text
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
            "RegionId": "NA",
            "Images": [ ... ],
            "MissingImages": []
          }
        ]
      }
    ]
  }
}
```

**Known `ImageTypeId` values:** `ECOMM`, `GHOST`, `SKETCH`, `SW`, `WORN`, `3DSKETCH`, `3D360`, `DETSTILL`

## Detection Logic

The app checks these requested image types:

| Result label | GImage `ImageTypeId` |
|---|---|
| Ecom | `ECOMM` |
| Ghost | `GHOST` |
| Swatch | `SW` |

A style/color pair has a region/type image when:

1. `data.Style.ImageTypes` contains the target `ImageTypeId`
2. The image type's `Colors` array contains the requested color
3. That color entry has a non-empty `Images` array
4. The color entry or image records have `RegionId` equal to `NA` or `EU`

When passing a specific color in `StyleImageQf.Colors`, the response usually only includes that color. The app still verifies the color value defensively before marking any result as available.

## Validated Test Cases

Live GImage data changes as assets are added. These examples were rechecked while adding the multi-type output:

| Style | Color | Availability summary |
|---|---|---|
| `W6GD57D0853` | `G011` | `NA Ecom`, `NA Swatch` |
| `W6GD57D0853` | `JBLK` | `NA Ecom`, `NA Swatch` |
| `W6GKA8D1103` | `AKRN` | `NA Ecom`, `NA Swatch`, `EU Swatch` |
| `E6GB00Z5371` | `G61Y` | `NA Ecom`, `EU Ecom`, `EU Ghost`, `EU Swatch` |
| `E6GB00Z5371` | `JBLK` | `NA Ecom`, `NA Swatch`, `EU Ghost`, `EU Swatch` |

## Source

Endpoint and request shape discovered by inspecting the JS bundle at:

```text
https://gimage.guess.com/browse/static/js/main.34436b26.js
```

API base URL defined in:

```text
https://gimage.guess.com/browse/config.js -> apiNamespace: "/browse/api/"
```
