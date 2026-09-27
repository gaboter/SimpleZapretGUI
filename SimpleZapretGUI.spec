# -*- mode: python ; coding: utf-8 -*-
# Сборка: pyinstaller --noconfirm SimpleZapretGUI.spec
block_cipher = None

a = Analysis(
    ["main.py"],
    pathex=["."],
    binaries=[],
    datas=[],
    hiddenimports=["PySide6.QtSvg", "PySide6.QtNetwork"],
    excludes=[
        "tkinter", "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets",
        "PySide6.QtQml", "PySide6.QtQuick", "PySide6.Qt3DCore", "PySide6.QtMultimedia",
        "PySide6.QtCharts", "PySide6.QtDataVisualization", "PySide6.QtPdf", "PySide6.QtBluetooth",
    ],
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="SimpleZapretGUI",
    icon="assets/icon.ico",
    version="version_info.txt",
    console=False,
    uac_admin=True,          # winws/WinDivert и службы требуют прав администратора
    upx=False,               # UPX повышает число ложных срабатываний антивирусов
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="SimpleZapretGUI")
