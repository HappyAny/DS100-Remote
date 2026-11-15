"""Compile firmware objects with an existing WSL ARM compiler."""

from pathlib import Path
import json
import subprocess
import hashlib

ROOT = Path(__file__).resolve().parents[1]


def run_linux(arguments):
    result = subprocess.run(["wsl.exe", "-d", "Ubuntu-24.04", "--", *arguments], cwd=ROOT,
                            capture_output=True, check=True)
    return result.stdout.decode("utf-8", errors="replace").strip()


def main():
    output = ROOT / "build/arm"
    output.mkdir(parents=True, exist_ok=True)
    version = run_linux(["arm-none-eabi-gcc", "--version"]).splitlines()[0]
    flags = ["-std=c99", "-mcpu=cortex-m4", "-mthumb", "-mfloat-abi=soft", "-ffreestanding",
             "-fno-builtin", "-ffunction-sections", "-fdata-sections", "-Os", "-g", "-Wall", "-Wextra", "-Werror"]
    files = []
    for name in ["ds100_remote", "ds100_v48_screen"]:
        run_linux(["arm-none-eabi-gcc", *flags, "-c", f"firmware/{name}.c", "-o", f"build/arm/{name}.o"])
        path = output / f"{name}.o"
        files.append({"file": str(path.relative_to(ROOT)), "size_bytes": path.stat().st_size,
                      "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    sizes = run_linux(["arm-none-eabi-size", "build/arm/ds100_remote.o", "build/arm/ds100_v48_screen.o"])
    receipt = {"compiler": version, "target": "ARM Cortex-M4 Thumb, soft float ABI", "objects": files,
               "section_sizes": sizes, "linked_into_stock_image": False, "flashed": False}
    (output / "build-receipt.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(receipt, indent=2))


if __name__ == "__main__":
    main()
