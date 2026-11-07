# -*- mode: python ; coding: utf-8 -*-
# Gera dist\ATENA\ATENA.exe   →   pyinstaller empacotamento\ATENA.spec --noconfirm
import os
RAIZ = os.path.abspath(os.path.join(SPECPATH, ".."))

a = Analysis(
    [os.path.join(RAIZ, "iniciar.py")],
    pathex=[RAIZ],
    datas=[
        (os.path.join(RAIZ, "atena", "web", "templates"), os.path.join("atena", "web", "templates")),
        (os.path.join(RAIZ, "atena", "web", "static"), os.path.join("atena", "web", "static")),
        (os.path.join(RAIZ, "atena", "recursos"), os.path.join("atena", "recursos")),
    ],
    hiddenimports=["waitress", "pypdf", "charset_normalizer", "openpyxl", "docx", "reportlab.graphics.barcode.common",
                   "reportlab.graphics.barcode.code128", "atena.web.rotas"],
    excludes=["tkinter", "matplotlib", "numpy", "pandas", "scipy", "IPython", "pytest", "playwright", "spacy", "torch"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="ATENA", console=True,
          icon=os.path.join(RAIZ, "empacotamento", "atena.ico"), version=None)
coll = COLLECT(exe, a.binaries, a.datas, name="ATENA")
