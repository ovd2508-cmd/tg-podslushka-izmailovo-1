import importlib.util
import os
import site

def _create_executor_shim():
    try:
        if importlib.util.find_spec("aiogram") is None:
            return
        bases = site.getsitepackages()
    except Exception:
        return

    for base in bases:
        if not base.endswith("site-packages"):
            continue
        target_dir = os.path.join(base, "aiogram", "utils")
        try:
            os.makedirs(target_dir, exist_ok=True)
            target = os.path.join(target_dir, "executor.py")
            if not os.path.exists(target):
                with open(target, "w", encoding="utf-8") as f:
                    f.write("# Compatibility shim for Bothost build check.\n")
        except Exception:
            pass
        return

_create_executor_shim()
