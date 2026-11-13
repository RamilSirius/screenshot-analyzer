# -*- mode: python ; coding: utf-8 -*-
# Сборка Mac-клиента (client_mac.py) в один .app бандл.
# Хоткеи через Carbon (RegisterEventHotKey) — Input Monitoring НЕ нужен,
# нужно только Screen Recording для скриншота (система спросит сама).

a = Analysis(
    ['client_mac.py'],
    pathex=[],
    binaries=[],
    datas=[('client.env', '.')],
    hiddenimports=[
        'pynput.mouse._darwin',
        'pynput.keyboard._darwin',
        'PIL._tkinter_finder',
        'AppKit',
        'Foundation',
        'objc',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='SystemApp2',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='SystemApp2',
)

app = BUNDLE(
    coll,
    name='SystemApp2.app',
    icon=None,
    bundle_identifier='com.systemapp.macos.v2',
    info_plist={
        'NSPrincipalClass': 'NSApplication',
        'NSHighResolutionCapable': 'True',
        'NSScreenCaptureUsageDescription': 'Needed to take screenshots for analysis',
        'LSUIElement': True,
    },
)
