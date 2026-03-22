"""Image compositing for grids (pick_your, gf_knows) and side-by-side (then_vs_2040)."""

from PIL import Image, ImageDraw, ImageFont

from config import (
    GRID_CELL_SIZE,
    GRID_PADDING,
    GRID_GAP,
    GRID_BG_COLOR,
    GRID_LABEL_SIZE,
    SIDE_BY_SIDE_PADDING,
    SIDE_BY_SIDE_DIVIDER_WIDTH,
    SIDE_BY_SIDE_ACCENT_COLOR,
)


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    h = hex_color.lstrip("#")
    return tuple(int(h[i : i + 2], 16) for i in (0, 2, 4))


def _get_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Try system fonts, fall back to Pillow default."""
    font_paths = [
        "/System/Library/Fonts/Helvetica.ttc",
        "/System/Library/Fonts/SFNSText.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ]
    for path in font_paths:
        try:
            return ImageFont.truetype(path, size)
        except (OSError, IOError):
            continue
    return ImageFont.load_default()


def _resize_crop(img: Image.Image, target_w: int, target_h: int) -> Image.Image:
    """Resize and center-crop to exact target dimensions."""
    # Scale to cover
    scale = max(target_w / img.width, target_h / img.height)
    new_w = int(img.width * scale)
    new_h = int(img.height * scale)
    img = img.resize((new_w, new_h), Image.LANCZOS)

    # Center crop
    left = (new_w - target_w) // 2
    top = (new_h - target_h) // 2
    return img.crop((left, top, left + target_w, top + target_h))


class Compositor:
    @staticmethod
    def create_grid(
        image_paths: list[str],
        output_path: str,
        labels: list[str] | None = None,
    ) -> str:
        """Create a 2x2 grid with numbered labels.

        Canvas: (cell*2 + gap + padding*2) square = 1124x1124 by default.
        """
        if len(image_paths) != 4:
            raise ValueError(f"Grid requires exactly 4 images, got {len(image_paths)}")

        cell = GRID_CELL_SIZE
        pad = GRID_PADDING
        gap = GRID_GAP
        canvas_size = cell * 2 + gap + pad * 2
        bg = _hex_to_rgb(GRID_BG_COLOR)

        canvas = Image.new("RGB", (canvas_size, canvas_size), bg)
        draw = ImageDraw.Draw(canvas)
        font = _get_font(28)

        if labels is None:
            labels = ["1", "2", "3", "4"]

        positions = [
            (pad, pad),                      # top-left
            (pad + cell + gap, pad),         # top-right
            (pad, pad + cell + gap),         # bottom-left
            (pad + cell + gap, pad + cell + gap),  # bottom-right
        ]

        for i, (img_path, (x, y)) in enumerate(zip(image_paths, positions)):
            img = Image.open(img_path).convert("RGB")
            img = _resize_crop(img, cell, cell)
            canvas.paste(img, (x, y))

            # Draw numbered label — white circle with black number, bottom-right
            label_r = GRID_LABEL_SIZE // 2
            inset = 12
            cx = x + cell - inset - label_r
            cy = y + cell - inset - label_r

            # Drop shadow
            draw.ellipse(
                [cx - label_r + 2, cy - label_r + 2, cx + label_r + 2, cy + label_r + 2],
                fill=(0, 0, 0, 128) if canvas.mode == "RGBA" else (30, 30, 30),
            )
            # White circle
            draw.ellipse(
                [cx - label_r, cy - label_r, cx + label_r, cy + label_r],
                fill=(255, 255, 255),
            )
            # Number text centered in circle
            label = labels[i]
            bbox = font.getbbox(label)
            tw = bbox[2] - bbox[0]
            th = bbox[3] - bbox[1]
            draw.text(
                (cx - tw // 2, cy - th // 2 - bbox[1]),
                label,
                fill=(0, 0, 0),
                font=font,
            )

        canvas.save(output_path, "PNG")
        return output_path

    @staticmethod
    def create_side_by_side(
        left_path: str,
        right_path: str,
        output_path: str,
        left_label: str = "Today",
        right_label: str = "2040",
    ) -> str:
        """Create side-by-side comparison with divider and labels.

        Canvas: (cell*2 + divider + padding*2) x (cell + padding*2).
        """
        cell = GRID_CELL_SIZE
        pad = SIDE_BY_SIDE_PADDING
        div_w = SIDE_BY_SIDE_DIVIDER_WIDTH
        accent = _hex_to_rgb(SIDE_BY_SIDE_ACCENT_COLOR)
        bg = _hex_to_rgb(GRID_BG_COLOR)

        canvas_w = cell * 2 + div_w + pad * 2
        canvas_h = cell + pad * 2
        canvas = Image.new("RGB", (canvas_w, canvas_h), bg)
        draw = ImageDraw.Draw(canvas)

        # Load and place images
        left_img = Image.open(left_path).convert("RGB")
        left_img = _resize_crop(left_img, cell, cell)
        canvas.paste(left_img, (pad, pad))

        right_img = Image.open(right_path).convert("RGB")
        right_img = _resize_crop(right_img, cell, cell)
        canvas.paste(right_img, (pad + cell + div_w, pad))

        # Divider line
        div_x = pad + cell
        draw.rectangle(
            [div_x, pad, div_x + div_w - 1, pad + cell - 1],
            fill=accent,
        )

        # "VS" text centered on divider
        vs_font = _get_font(24)
        vs_bbox = vs_font.getbbox("VS")
        vs_w = vs_bbox[2] - vs_bbox[0]
        vs_h = vs_bbox[3] - vs_bbox[1]
        vs_x = div_x + div_w // 2 - vs_w // 2
        vs_y = pad + cell // 2 - vs_h // 2

        # Background pill for VS
        pill_pad = 8
        draw.rounded_rectangle(
            [vs_x - pill_pad, vs_y - pill_pad, vs_x + vs_w + pill_pad, vs_y + vs_h + pill_pad],
            radius=6,
            fill=bg,
        )
        draw.text((vs_x, vs_y - vs_bbox[1]), "VS", fill=(255, 255, 255), font=vs_font)

        # Bottom labels — semi-transparent bar with text
        label_font = _get_font(20)
        bar_h = 36

        # Left label
        draw.rectangle(
            [pad, pad + cell - bar_h, pad + cell, pad + cell],
            fill=(0, 0, 0),  # solid black as fallback (no alpha in RGB)
        )
        lbbox = label_font.getbbox(left_label)
        lw = lbbox[2] - lbbox[0]
        draw.text(
            (pad + cell // 2 - lw // 2, pad + cell - bar_h + (bar_h - (lbbox[3] - lbbox[1])) // 2 - lbbox[1]),
            left_label,
            fill=(255, 255, 255),
            font=label_font,
        )

        # Right label
        rx = pad + cell + div_w
        draw.rectangle(
            [rx, pad + cell - bar_h, rx + cell, pad + cell],
            fill=(0, 0, 0),
        )
        rbbox = label_font.getbbox(right_label)
        rw = rbbox[2] - rbbox[0]
        draw.text(
            (rx + cell // 2 - rw // 2, pad + cell - bar_h + (bar_h - (rbbox[3] - rbbox[1])) // 2 - rbbox[1]),
            right_label,
            fill=accent,
            font=label_font,
        )

        canvas.save(output_path, "PNG")
        return output_path
