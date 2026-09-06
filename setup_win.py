"""
LuminoAI Windows Native C-Binary Build Script
============================================
Compiles Cython C sources (.c or .py) into native Windows PE32+ .pyd extensions using MSVC.
Used by GitHub Actions Windows runner and local Windows development.
"""

from setuptools import setup, Extension
import os
import sys

modules = [
    "security_core",
    "audit_engine",
    "cropper_engine",
    "el_reader_engine",
    "process_tif",
    "batch_cropper"
]

ext_modules = []
for mod in modules:
    c_file = f"{mod}.c"
    py_file = f"{mod}.py"
    if os.path.exists(c_file):
        ext_modules.append(Extension(mod, sources=[c_file]))
    elif os.path.exists(py_file):
        try:
            from Cython.Build import cythonize
            ext = cythonize(
                [py_file],
                compiler_directives={'language_level': '3', 'always_allow_keywords': True},
                quiet=True
            )
            ext_modules.extend(ext)
        except ImportError:
            print(f"⚠️ Cython not found, skipping {py_file}")

if ext_modules:
    setup(
        name="LuminoAI-Windows-Binaries",
        ext_modules=ext_modules,
        options={'build_ext': {'inplace': True}}
    )
    print("✅ Windows C-extensions build complete!")
else:
    print("⚠️ No modules found to compile.")
