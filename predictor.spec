# -*- mode: python ; coding: utf-8 -*-

block_cipher = None

a = Analysis(
    ['server.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('index.html', '.'),
        ('footy', 'footy'),
        # NOTE: the API-Football key is intentionally NOT bundled. To enable the
        # (rarely used) API-Football fallback, set the APIFOOTBALL_KEY env var or
        # drop a cache/apifootball_key.txt next to the .exe.
    ],
    hiddenimports=[
        'footy',
        'footy.paths',
        'footy.whoscored',
        'footy.apifootball',
        'footy.sportsdb',
        'footy.flashscore',
        'footy.form',
        'footy.predict',
        'footy.simulate',
        'footy.fixtures',
        'seleniumbase',
        'seleniumbase.fixtures',
        'flask',
        'numpy',
        'requests',
        'curl_cffi',
        'curl_cffi.requests',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='FootballPredictor',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,  # Show console for debugging
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)
