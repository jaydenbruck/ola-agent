"""Draw the same two-dot mark used by the SwiftUI header, without external artwork."""
import json
from pathlib import Path
from PIL import Image, ImageDraw

catalog = Path(__file__).resolve().parents[1] / "Ola" / "Assets.xcassets"
target = catalog / "AppIcon.appiconset"
target.mkdir(parents=True, exist_ok=True)
info = {"author": "xcode", "version": 1}
(catalog / "Contents.json").write_text(json.dumps({"info": info}, indent=2))
master = Image.new("RGB", (1024, 1024), (246, 246, 248))
draw = ImageDraw.Draw(master)
draw.ellipse((164, 164, 860, 860), fill=(20, 21, 25))
for left in [402, 562]:
    draw.rounded_rectangle((left, 462, left + 60, 538), radius=30, fill="white")
images = []
for points in [20, 29, 40, 60]:
    for scale in [2, 3]:
        size = points * scale
        filename = f"icon-{points}@{scale}x.png"
        master.resize((size, size), Image.Resampling.LANCZOS).save(target / filename)
        images.append({"idiom": "iphone", "size": f"{points}x{points}", "scale": f"{scale}x", "filename": filename})
master.save(target / "icon-1024.png")
images.append({"idiom": "ios-marketing", "size": "1024x1024", "scale": "1x", "filename": "icon-1024.png"})
(target / "Contents.json").write_text(json.dumps({"images": images, "info": info}, indent=2))
