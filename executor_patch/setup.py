from setuptools import setup
from setuptools.command.install import install
import os
import site

SHIM = """# Compatibility shim for hosting platforms that expect aiogram 2 executor API.\n# This project uses aiogram 3; do not use this module in application code.\n"""

class InstallWithShim(install):
    def run(self):
        super().run()
        candidates = site.getsitepackages()
        target = None
        for base in candidates:
            candidate = os.path.join(base, "aiogram", "utils")
            if os.path.isdir(os.path.dirname(candidate)) or base.endswith("site-packages"):
                target = candidate
                break
        if not target:
            raise RuntimeError("Не найден site-packages для установки compatibility shim")
        os.makedirs(target, exist_ok=True)
        with open(os.path.join(target, "executor.py"), "w", encoding="utf-8") as f:
            f.write(SHIM)

setup(
    name="executor-patch",
    version="0.0.1",
    description="Compatibility shim for Bothost aiogram executor check",
    cmdclass={"install": InstallWithShim},
)
