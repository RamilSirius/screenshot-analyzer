# -*- mode: python ; coding: utf-8 -*-

a = Analysis(
    ['client_fixed.py'],
    pathex=[],
    binaries=[],
    datas=[('client.env', '.')],
    hiddenimports=[
        'pynput.keyboard._darwin',
        'pynput.mouse._darwin',
        'pynput._util.darwin',
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
    name='SystemApp',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
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
    upx=True,
    upx_exclude=[],
    name='SystemApp',
)

app = BUNDLE(
    coll,
    name='SystemApp.app',
    icon=None,
    bundle_identifier='com.systemapp.macos',
    info_plist={
        'NSPrincipalClass': 'NSApplication',
        'NSHighResolutionCapable': 'True',
        'NSInputMonitoringUsageDescription': 'Needed for global hotkeys (Shift+A, Shift+D)',
        'NSScreenCaptureUsageDescription': 'Needed to take screenshots for analysis',
        'LSUIElement': True,
    },
)
