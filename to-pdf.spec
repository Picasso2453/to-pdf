# PyInstaller spec: one-file windowed build of To PDF.
# Build with:  .venv\Scripts\python -m PyInstaller --noconfirm --clean to-pdf.spec

a = Analysis(
    ["run_to_pdf.py"],
    pathex=[],
    datas=[("to_pdf/assets", "to_pdf/assets")],
    hiddenimports=[],
    excludes=[
        "tkinter", "numpy", "pytest", "pypdf",
        "PySide6.QtPdf", "PySide6.QtNetwork", "PySide6.QtQml", "PySide6.QtQuick",
        "PySide6.QtWebEngineCore", "PySide6.QtMultimedia", "PySide6.Qt3DCore",
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="To PDF",
    icon="to_pdf/assets/icon.ico",
    version="tools/version_info.txt",
    console=False,
    upx=False,
    runtime_tmpdir=None,
)
