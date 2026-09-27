"""Генерирует assets/icon.ico из логотипа приложения (запускается перед сборкой)."""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication  # noqa: E402
from PIL import Image  # noqa: E402

app = QApplication([])
from simplezapretgui.ui import icons  # noqa: E402

out = ROOT / "assets"
out.mkdir(exist_ok=True)
tmp = out / "_icon256.png"
icons.logo_pixmap(256).save(str(tmp))
im = Image.open(tmp).convert("RGBA")
im.save(out / "icon.ico", sizes=[(s, s) for s in (256, 128, 64, 48, 32, 16)])
im.close()
tmp.unlink()
print("assets/icon.ico created")
