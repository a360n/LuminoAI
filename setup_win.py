"""
LuminoAI Windows Native C-Binary Build Script
============================================
Compiles Cython C sources (.c) into native Windows PE32+ .pyd extensions using MSVC.
Used by GitHub Actions Windows runner and local Windows development.
"""

import os
import sys

# Force UTF-8 encoding on Windows to prevent UnicodeEncodeError (cp1252)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from setuptools import setup, Extension

# Windows MSVC compatibility macros:
win_macros = [
    ("CYTHON_FAST_THREAD_STATE", "0"),
    ("CYTHON_USE_TYPE_SPECS", "0"),
]

win_compile_args = []
if sys.platform == "win32":
    win_compile_args.append("/utf-8")

modules = [
    "security_core",
    "audit_engine",
    "cropper_engine",
    "el_reader_engine",
    "process_tif",
    "batch_cropper",
    "main"
]

ext_modules = []
for mod in modules:
    c_file = f"{mod}.c"
    py_file = f"{mod}.py"
    if os.path.exists(c_file):
        ext_modules.append(Extension(
            mod,
            sources=[c_file],
            define_macros=win_macros,
            extra_compile_args=win_compile_args
        ))
    elif os.path.exists(py_file):
        try:
            from Cython.Build import cythonize
            ext = cythonize(
                [py_file],
                compiler_directives={'language_level': '3', 'always_allow_keywords': True, 'annotation_typing': False},
                quiet=True
            )
            ext_modules.extend(ext)
        except ImportError:
            print(f"[WARN] Cython not found, skipping {py_file}")

if __name__ == '__main__':
    successful = 0
    failed = []
    
    print(f"[INFO] Starting compilation of {len(ext_modules)} modules on Python {sys.version}...")
    for ext in ext_modules:
        print(f"\n[BUILD] Compiling {ext.name}...")
        try:
            setup(
                name=ext.name,
                ext_modules=[ext],
                options={'build_ext': {'inplace': True}},
                script_args=['build_ext', '--inplace']
            )
            print(f"[OK] {ext.name} compiled successfully!")
            successful += 1
        except Exception as e:
            print(f"[ERROR] Failed to compile {ext.name}: {e}")
            failed.append(ext.name)
            
    print(f"\n==========================================================")
    print(f"[SUMMARY] Build Results: {successful} succeeded, {len(failed)} failed")
    if failed:
        print(f"[WARN] Failed modules: {failed}")
    print(f"==========================================================")
    
    if successful == 0 and ext_modules:
        sys.exit(1)
