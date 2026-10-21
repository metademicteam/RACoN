"""Generate Tauri and Windows application icons for RACoN Desktop."""
from pathlib import Path
from PIL import Image, ImageDraw

icons_dir = Path("apps/desktop/src-tauri/icons")
icons_dir.mkdir(parents=True, exist_ok=True)

# Generate a high-res 512x512 base icon
size = 512
img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
draw = ImageDraw.Draw(img)

# Outer rounded rectangle (dark slate background)
margin = 24
draw.rounded_rectangle([margin, margin, size - margin, size - margin], radius=90, fill="#161b22", outline="#30363d", width=8)

# Center circular node (primary blue)
center = size // 2
draw.ellipse([center - 110, center - 110, center + 110, center + 110], fill="#1f6feb", outline="#58a6ff", width=10)

# Inner satellite nodes representing distributed compute
satellites = [
    (center - 140, center - 80, 28, "#f472b6"),
    (center + 140, center - 80, 28, "#3fb950"),
    (center, center + 160, 32, "#d29922"),
    (center - 110, center + 110, 24, "#a371f7"),
    (center + 110, center + 110, 24, "#58a6ff"),
]

for x, y, r, color in satellites:
    draw.line([center, center, x, y], fill="#30363d", width=6)
    draw.ellipse([x - r, y - r, x + r, y + r], fill=color, outline="#ffffff", width=4)

# Central spark icon / letter 'R'
draw.ellipse([center - 45, center - 45, center + 45, center + 45], fill="#ffffff")

# Save various sizes
img.save(icons_dir / "512x512.png")
img.resize((256, 256), Image.Resampling.LANCZOS).save(icons_dir / "128x128@2x.png")
img.resize((128, 128), Image.Resampling.LANCZOS).save(icons_dir / "128x128.png")
img.resize((32, 32), Image.Resampling.LANCZOS).save(icons_dir / "32x32.png")

# Generate multi-resolution Windows .ico
img.save(
    icons_dir / "icon.ico",
    format="ICO",
    sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
)

# Also save a copy for root and desktop shortcut
img.save(Path("apps/desktop/icon.png"))
img.save(Path("apps/desktop/icon.ico"), format="ICO", sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])

print("Successfully generated all RACoN application icons in", icons_dir)
