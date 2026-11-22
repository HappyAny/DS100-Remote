# Third-party components

The project source uses GPL-3.0-only. This license does not change the licenses or rights of third-party components.

| Component | Source | License or rights |
| --- | --- | --- |
| DS100 V4.8 binary and decoded research image | ALIENTEK official repository, commit `46670a349c54e7b9f07a6f7e832abee5dfeb4e13` | Original vendor rights apply. The project does not grant a license for the vendor binary. |
| Noto Sans CJK SC Regular | `notofonts/noto-cjk`, commit `f8d157532fbfaeda587e826d4cd5b21a49186f7c` | SIL Open Font License 1.1; full text in `assets/fonts/OFL.txt`. |
| PySide6 | Qt for Python | The installed distribution retains its LGPL/GPL or commercial terms. It is a dependency, not part of the project license. |
| NumPy and Pillow | Respective upstream projects | Their installed distributions retain their own licenses. |
| CPython and PyInstaller | Respective upstream projects | The Windows archives include the Python and PyInstaller license files. |
| Capstone, Unicorn, pytest | Respective upstream projects | Research and test dependencies retain their own licenses. |

The source ZIP and application wheel include the Noto font and its license. They do not include the vendor firmware, decoded images, device backups, candidate firmware, the ARM compiler, or the test dependencies.
The separate Windows application folder includes the required Python, Qt, NumPy, Pillow, and runtime files. Its `third-party-licenses` directory contains the license files from the installed distributions and a version receipt. The Windows ZIP also includes this project's corresponding source ZIP.

The rle7 Windows application uses Qt 6.10.1, PySide6 6.10.1, and Shiboken6 6.10.1. The Qt components retain their upstream LGPLv3 or GPL terms. The archive includes the LGPLv3 and GPLv3 license texts. The Qt DLLs are separate files under `_internal/PySide6`. Users can replace interface-compatible libraries or build the application with their own Qt for Python installation. [Qt licensing](https://doc.qt.io/qt-6.10/licensing.html)

- [Qt 6.10.1 source](https://download.qt.io/official_releases/qt/6.10/6.10.1/single/)
- [PySide6 and Shiboken6 6.10.1 source](https://download.qt.io/official_releases/QtForPython/pyside6/PySide6-6.10.1-src/)
- [Qt third-party notices](https://doc.qt.io/qt-6.10/licenses-used-in-qt.html)
The font receipt records its size, SHA-256, Git blob hash, upstream path, and pinned commit. The bundled font matches Git blob `dc15562470b4f842321894787a0d066879ccff8b`.
Microsoft YaHei is an optional font loaded from a local Windows installation. The project does not distribute it.
