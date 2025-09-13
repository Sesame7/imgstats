# gen_images.py (simplified English version)
# Generates folder structure: ./IMAGES/<station>/<model>/<PASS>-<YYYYMMDD>-<HHMMSS>-<COUNT>.jpg
# Automatically continues COUNT by scanning existing files.

import random
import re
from datetime import datetime, timedelta
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

# ===== Config =====
ROOT = Path("./IMAGES")           # Root directory
STATIONS = ["S9", "S7-D2", "S4"]  # Station list
MODELS = ["OR-3CT", "OR-2CT"]     # Model list
TOTAL_PER_COMBO = 200             # Images per station×model
OK_RATIO = 0.9                    # OK probability (rest are NG)
START_TIME_OFFSET_H = 2           # Start time offset (hours before now)
TIME_STEP_SEC = 30                # Time interval between images
IMG_SIZE = (640, 480)             # Image size
FONT_SIZE = 20                    # Font size

# Filename regex to extract count
NAME_RE = re.compile(r'^(OK|NG)-\d{8}-\d{6}-(\d+)\.(?:jpg|jpeg|png)$', re.IGNORECASE)

def latest_count_in(dir_path: Path) -> int:
    """Return max count found in existing files of dir_path."""
    if not dir_path.exists():
        return 0
    mx = 0
    for p in dir_path.iterdir():
        if not p.is_file():
            continue
        m = NAME_RE.match(p.name)
        if m:
            try:
                c = int(m.group(2))
                if c > mx:
                    mx = c
            except ValueError:
                pass
    return mx

def draw_image(lines, bg):
    img = Image.new("RGB", IMG_SIZE, bg)
    d = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("arial.ttf", FONT_SIZE)
    except:
        font = ImageFont.load_default()

    # center multiline text
    line_sizes = [d.textbbox((0, 0), t, font=font) for t in lines]
    heights = [(b[3] - b[1]) for b in line_sizes]
    widths  = [(b[2] - b[0]) for b in line_sizes]
    total_h = sum(heights) + 6 * (len(lines) - 1)
    y = (IMG_SIZE[1] - total_h) // 2
    for t, w, h in zip(lines, widths, heights):
        x = (IMG_SIZE[0] - w) // 2
        d.text((x, y), t, fill=(0, 0, 0), font=font)
        y += h + 6
    return img

def main():
    random.seed()
    created = 0
    ROOT.mkdir(parents=True, exist_ok=True)
    start_time = datetime.now() - timedelta(hours=START_TIME_OFFSET_H)

    for station in STATIONS:
        for model in MODELS:
            out_dir = ROOT / station / model
            out_dir.mkdir(parents=True, exist_ok=True)

            base_count = latest_count_in(out_dir)
            for i in range(TOTAL_PER_COMBO):
                ts = start_time + timedelta(seconds=TIME_STEP_SEC * i)
                label = "OK" if random.random() < OK_RATIO else "NG"
                count = base_count + i + 1

                fname = f"{label}-{ts.strftime('%Y%m%d')}-{ts.strftime('%H%M%S')}-{count}.jpg"
                fpath = out_dir / fname

                bg = (180, 235, 180) if label == "OK" else (230, 60, 60)
                lines = [
                    f"{station} | {model}",
                    f"{label}  #{count}",
                    ts.strftime("%Y-%m-%d %H:%M:%S")
                ]
                img = draw_image(lines, bg)
                img.save(fpath, "JPEG", quality=85)
                created += 1

    print(f"Generated {created} images under {ROOT}")
    print("Example path:", ROOT / STATIONS[0] / MODELS[0])

if __name__ == "__main__":
    main()
