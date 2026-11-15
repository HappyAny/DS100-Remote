import numpy as np
import pytest
from ds100.storage import load_csv


def test_csv_keeps_headers_and_ignores_metadata(tmp_path):
    path = tmp_path / "scope.csv"
    path.write_text("DS100 export,probe:X10\nTime(s),CHA(V),CHB(V)\n0,1.2,-1,\n0.000001,1.4,-0.8,\n0.000002,1.6,-0.6,\n", encoding="utf-8")
    x, channels, metadata = load_csv(path)
    assert np.allclose(x, [0, 1e-6, 2e-6])
    assert np.allclose(channels[0], [1.2, 1.4, 1.6])
    assert metadata["xlabel"] == "Time(s)"
    assert metadata["units_verified"] is False


def test_single_column_csv_uses_sample_indices(tmp_path):
    path = tmp_path / "samples.csv"
    path.write_text("Amplitude\n0.1\n0.2\n0.3\n", encoding="utf-8")
    x, channels, metadata = load_csv(path)
    assert x.tolist() == [0, 1, 2]
    assert metadata["xlabel"] == "采样点"


def test_stock_ds100_csv_has_two_voltage_columns_and_sample_rate(tmp_path):
    path = tmp_path / "000.CSV"
    path.write_text("CHA(mV)  probe:X1,CHB(mV)  probe:X1,sampling rate : 500000\n"
                    "  0,1.90\n398,5.84\n  0,5.84\n", encoding="ascii")
    x, channels, meta = load_csv(path)
    assert np.allclose(x, [0, 2e-6, 4e-6])
    assert np.allclose(channels[0], [0, 0.398, 0])
    assert np.allclose(channels[1], [0.00190, 0.00584, 0.00584])
    assert meta["sample_rate_hz"] == 500000 and meta["units_verified"] is True
    assert meta["source_units"] == {0: "mV", 1: "mV"}
    assert meta["physical_calibration_verified"] is False


def test_stock_single_b_channel_preserves_identity_and_probe_metadata(tmp_path):
    path = tmp_path / "b.csv"
    path.write_text("CHB(V) probe:X10,sampling rate : 1000\n1.2\n1.4\n", encoding="ascii")
    x, channels, meta = load_csv(path)
    assert np.allclose(x, [0, .001]) and set(channels) == {1}
    assert np.allclose(channels[1], [1.2, 1.4])
    assert meta["probe_factors"] == {1: 10}
    assert meta["probe_multiplier_applied"] is False


@pytest.mark.parametrize("rate", ["0", "-1", "nan", "bad"])
def test_stock_csv_rejects_invalid_rate(tmp_path, rate):
    path = tmp_path / "bad.csv"
    path.write_text(f"CHA(mV) probe:X1,sampling rate : {rate}\n0\n1\n", encoding="ascii")
    with pytest.raises(ValueError, match="sampling rate"):
        load_csv(path)


@pytest.mark.parametrize("row", ["nan,1", "bad,1", "1,2,3"])
def test_stock_csv_rejects_bad_rows_without_compressing_time_axis(tmp_path, row):
    path = tmp_path / "bad.csv"
    path.write_text("CHA(V) probe:X1,CHB(mV) probe:X1,sampling rate : 1e6\n0,0\n"
                    + row + "\n1,1\n", encoding="ascii")
    with pytest.raises(ValueError, match="sample row"):
        load_csv(path)
