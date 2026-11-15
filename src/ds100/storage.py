"""Read stock DS100 files. All device file operations use read access."""

from collections import Counter
from pathlib import Path
import csv
import ctypes
import hashlib
import io
import os
import re
import numpy as np


def find_drives():
    """List labeled storage candidates; the label does not prove USB identity."""
    if os.name != "nt":
        return []
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    mask = kernel.GetLogicalDrives()
    result = []
    for index in range(26):
        if not mask & (1 << index):
            continue
        root = f"{chr(65 + index)}:/"
        label = ctypes.create_unicode_buffer(261)
        if kernel.GetVolumeInformationW(ctypes.c_wchar_p(root), label, len(label), None, None, None, None, 0) and label.value == "ATK-DSO":
            result.append(Path(root))
    return result


def list_files(root):
    return sorted((path for path in Path(root).iterdir() if path.is_file() and path.suffix.lower() in (".csv", ".bmp")), key=lambda path: path.name.lower())


def read_cfg(root):
    data = (Path(root) / "CFG").read_bytes()
    if len(data) != 480:
        raise ValueError("The device configuration does not have the expected 480-byte size.")
    return {"size": len(data), "sha256": hashlib.sha256(data).hexdigest(), "bytes": data}


def _stock_csv(fields, rows, path):
    spec = re.compile(r"CH([AB])\s*\(\s*(V|mV|uV|µV|μV)\s*\)\s*probe\s*:\s*[Xx×]?\s*([0-9]+(?:\.[0-9]+)?)", re.IGNORECASE)
    columns, sample_rate = [], None
    multipliers = {"v": 1.0, "mv": 1e-3, "uv": 1e-6, "µv": 1e-6, "μv": 1e-6}
    for field in fields:
        match = spec.fullmatch(field)
        if match:
            channel, unit, probe = match.groups()
            probe = float(probe)
            if probe <= 0:
                raise ValueError("The CSV probe factor must be greater than zero.")
            columns.append((ord(channel.upper()) - ord("A"), unit, probe))
        elif re.match(r"sampling\s+rate\s*:", field, re.IGNORECASE):
            try:
                sample_rate = float(field.split(":", 1)[1].strip())
            except ValueError as error:
                raise ValueError("The CSV sampling rate is invalid.") from error
        else:
            raise ValueError("The DS100 CSV header contains an unsupported channel or unit.")
    if not columns or len(columns) > 2 or len({item[0] for item in columns}) != len(columns):
        raise ValueError("The DS100 CSV channel header is invalid.")
    if sample_rate is None or not np.isfinite(sample_rate) or sample_rate <= 0:
        raise ValueError("The CSV sampling rate must be finite and greater than zero.")
    samples = []
    for row in rows:
        try:
            values = [float(field) for field in row]
        except ValueError as error:
            raise ValueError("The DS100 CSV contains an invalid sample row.") from error
        if len(values) != len(columns) or not np.isfinite(values).all():
            raise ValueError("The DS100 CSV contains an invalid sample row.")
        samples.append(values)
    if len(samples) < 2:
        raise ValueError("The CSV file must contain at least two sample rows.")
    array = np.asarray(samples, dtype=float)
    # The file values carry the declared voltage unit. Keep probe factors as
    # metadata: do not apply a second probe multiplier to exported values.
    channels = {channel: array[:, column] * multipliers[unit.lower()]
                for column, (channel, unit, _) in enumerate(columns)}
    return np.arange(len(array), dtype=float) / sample_rate, channels, {
        "format": "ds100-stock", "headers": [fields], "column_names": fields[:len(columns)],
        "xlabel": "时间 / s", "ylabel": "电压 / V（按 CSV 单位）", "source": str(path),
        "sample_rate_hz": sample_rate, "sample_count": len(array),
        "source_units": {channel: unit for channel, unit, _ in columns},
        "probe_factors": {channel: probe for channel, _, probe in columns},
        "probe_multiplier_applied": False, "units_verified": True,
        "physical_calibration_verified": False,
    }


def load_csv(path):
    data = Path(path).read_bytes()
    if len(data) > 32 * 1024 * 1024:
        raise ValueError("The CSV file exceeds the 32 MiB size limit.")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("gb18030")
    delimiter = ";" if text.count(";") > text.count(",") else ","
    fields_list = []
    for row in csv.reader(io.StringIO(text), delimiter=delimiter):
        fields = [field.strip() for field in row]
        while fields and not fields[-1]:
            fields.pop()
        if not fields:
            continue
        fields_list.append(fields)
    for index, fields in enumerate(fields_list):
        if any(re.match(r"CH[AB]\s*\(", field, re.IGNORECASE) for field in fields) and any(
                re.match(r"sampling\s+rate\s*:", field, re.IGNORECASE) for field in fields):
            return _stock_csv(fields, fields_list[index + 1:], path)
    rows, headers = [], []
    for fields in fields_list:
        try:
            values = [float(field) for field in fields]
        except ValueError:
            headers.append(fields)
            continue
        if 1 <= len(values) <= 8 and np.isfinite(values).all():
            rows.append(values)
    if not rows:
        raise ValueError("The CSV file contains no supported numeric sample rows.")
    width = Counter(len(row) for row in rows).most_common(1)[0][0]
    array = np.asarray([row for row in rows if len(row) == width], dtype=float)
    if len(array) < 2:
        raise ValueError("The CSV file must contain at least two sample rows.")
    names = next((row for row in reversed(headers) if len(row) == width), [f"列 {i + 1}" for i in range(width)])
    if width == 1:
        x, channels, xlabel = np.arange(len(array)), {0: array[:, 0]}, "采样点"
    else:
        x, channels = array[:, 0], {index - 1: array[:, index] for index in range(1, width)}
        xlabel = names[0]
    return x, channels, {"format": "generic", "headers": headers, "column_names": names,
                         "xlabel": xlabel, "ylabel": "数据值 · 单位见 CSV", "source": str(path), "units_verified": False}
