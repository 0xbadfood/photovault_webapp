from PIL import Image, ImageDraw
import os

base_dir = "../data/testuser/myiphone/files"

colors = ['red', 'green', 'blue', 'yellow', 'purple']
for color in colors:
    img = Image.new('RGB', (800, 600), color=color)
    d = ImageDraw.Draw(img)
    d.text((10,10), f"Hello {color}", fill=(255,255,255))
    img.save(os.path.join(base_dir, f"test_{color}.jpg"))

print("Created dummy images")
