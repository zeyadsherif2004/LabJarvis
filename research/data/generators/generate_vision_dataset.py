"""Render the clearly-synthetic reagent-label image dataset.

Every image is a programmatically drawn mock label (text on a plain label
shape). No photographs of real containers are used and no image implies real
chemical contents. ``truth`` is the physical ground truth of the drawn label:
``match`` (the label is the expected reagent), ``mismatch`` (it is not, or the
printed text is defaced/misspelled), or ``unreadable`` (blank label). OCR
degradations (rotation, blur, contrast, occlusion, glare) never change the
physical truth.
"""

from __future__ import annotations

import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont

DATA_DIR = Path(__file__).resolve().parents[1]
MANIFEST = DATA_DIR / "manifests" / "label-images.jsonl"
MEDIA_DIR = DATA_DIR / "media" / "vision"

SIZE = (640, 320)
FONT_DIR = Path("C:/Windows/Fonts")

GROUPS: list[dict[str, str]] = [
    {"expected": "ethanol", "similar": "methanol", "dissimilar": "acetone",
     "misspelling": "ETHANOI"},
    {"expected": "methanol", "similar": "ethanol", "dissimilar": "sodium chloride",
     "misspelling": "METHAN0L"},
    {"expected": "sodium chloride", "similar": "sodium chlorate", "dissimilar": "isopropanol",
     "misspelling": "SODIUM CHL0RIDE"},
    {"expected": "isopropanol", "similar": "propanol", "dissimilar": "glycerol",
     "misspelling": "ISOPROPANOI"},
    {"expected": "acetone", "similar": "acetonitrile", "dissimilar": "distilled water",
     "misspelling": "ACET0NE"},
]


def _font(name: str, size: int) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(str(FONT_DIR / name), size)
    except OSError:
        return ImageFont.truetype(str(FONT_DIR / "arial.ttf"), size)


def draw_label(
    text: str,
    *,
    script: bool = False,
    contrast_fill: str = "black",
) -> Image.Image:
    image = Image.new("RGB", SIZE, "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle([8, 8, SIZE[0] - 8, SIZE[1] - 8], outline="#444444", width=4)
    main_font_name = "segoesc.ttf" if script else "arialbd.ttf"
    size = 84
    font = _font(main_font_name, size)
    while draw.textlength(text.upper(), font=font) > SIZE[0] - 60 and size > 24:
        size -= 6
        font = _font(main_font_name, size)
    width = draw.textlength(text.upper(), font=font)
    draw.text(((SIZE[0] - width) / 2, 110), text.upper(), fill=contrast_fill, font=font)
    # Note: the production comparison is against the full visible text, so any
    # extra printed text (lot numbers, concentrations) prevents an exact MATCH.
    # Labels therefore carry the reagent name only; extra-text brittleness is
    # probed separately in the scenario dataset.
    return image


def variant(image: Image.Image, name: str) -> Image.Image:
    if name == "clean" or name == "script":
        return image
    if name == "rot15":
        return image.rotate(15, expand=True, fillcolor="white")
    if name == "rot30":
        return image.rotate(30, expand=True, fillcolor="white")
    if name == "blur3":
        return image.filter(ImageFilter.GaussianBlur(3))
    if name == "blur8":
        return image.filter(ImageFilter.GaussianBlur(8))
    if name == "lowcontrast":
        return ImageEnhance.Contrast(image).enhance(0.22)
    if name == "occlusion":
        occluded = image.copy()
        ImageDraw.Draw(occluded).rectangle([220, 70, 430, 180], fill="#9a9a9a")
        return occluded
    if name == "glare":
        glared = image.convert("RGBA")
        overlay = Image.new("RGBA", glared.size, (0, 0, 0, 0))
        ImageDraw.Draw(overlay).ellipse([140, 30, 520, 200], fill=(255, 255, 255, 190))
        return Image.alpha_composite(glared, overlay).convert("RGB")
    if name == "small":
        shrunk = image.resize((SIZE[0] // 5, SIZE[1] // 5))
        return shrunk.resize(SIZE)
    raise ValueError(name)


def main() -> None:
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, object]] = []
    index = 0

    def add(
        expected: str,
        printed: str | None,
        variant_name: str,
        truth: str,
        *,
        script: bool = False,
    ) -> None:
        nonlocal index
        index += 1
        image_id = f"label-{index:03d}"
        if printed is None:
            base = Image.new("RGB", SIZE, "white")
            draw = ImageDraw.Draw(base)
            draw.rectangle([8, 8, SIZE[0] - 8, SIZE[1] - 8], outline="#444444", width=4)
        else:
            base = draw_label(
                printed,
                script=script,
                contrast_fill="#c9c9c9" if variant_name == "lowcontrast" else "black",
            )
        rendered = variant(base, variant_name)
        path = MEDIA_DIR / f"{image_id}.png"
        rendered.save(path)
        records.append(
            {
                "id": image_id,
                "modality": "vision",
                "synthetic": True,
                "expected_reagent": expected,
                "printed_text": printed or "",
                "variant": variant_name,
                "truth": truth,
                "media_path": f"../media/vision/{image_id}.png",
                "content_type": "image/png",
            }
        )

    for group in GROUPS:
        expected = group["expected"]
        for name in ["clean", "rot15", "rot30", "blur3", "lowcontrast",
                     "occlusion", "glare", "script"]:
            add(expected, expected, name, "match", script=name == "script")
        for name in ["clean", "rot15", "blur3", "lowcontrast"]:
            add(expected, group["similar"], name, "mismatch")
        for name in ["clean", "blur3"]:
            add(expected, group["dissimilar"], name, "mismatch")
        for name in ["clean", "small"]:
            add(expected, group["misspelling"], name, "mismatch")
        add(expected, None, "clean", "unreadable")
        add(expected, expected, "blur8", "match")

    with MANIFEST.open("w", encoding="utf-8", newline="\n") as handle:
        for record in records:
            handle.write(json.dumps(record, sort_keys=True) + "\n")
    print(f"wrote {len(records)} labels to {MANIFEST}")


if __name__ == "__main__":
    main()
