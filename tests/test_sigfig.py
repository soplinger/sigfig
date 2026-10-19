import json
from decimal import Decimal
from pathlib import Path

import pytest

import sigfig
from sigfig import (
    WorksheetError,
    calc,
    count_sig_figs,
    display_pair,
    explain_sig_figs,
    main,
    parse_worksheet,
    propagate_items,
    round_places,
    round_sig,
    stats_report,
    to_sci,
)

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


def _rows(text):
    items, problems = parse_worksheet(text)
    assert problems == []
    return {row.name: row for row in propagate_items(items)}


def _pair(text):
    items, _ = parse_worksheet(text)
    row = propagate_items(items)[-1]
    value, unc = display_pair(row.v, row.u)
    return format(value, "f"), format(unc, "f")


def test_count_sig_figs_rules():
    assert count_sig_figs("12.30") == (4, False)
    assert count_sig_figs("0.00450") == (3, False)
    assert count_sig_figs("1.20e2") == (3, False)
    assert count_sig_figs("0.5") == (1, False)
    assert count_sig_figs("0") == (1, False)


def test_trailing_zeros_without_point_are_flagged_ambiguous():
    assert count_sig_figs("100") == (1, True)
    notes = explain_sig_figs("100")
    assert any("ambiguous" in note for note in notes)


def test_round_sig_carries_and_rounds_half_up():
    assert format(round_sig("9.96", 2), "f") == "10"
    assert format(round_sig("1234.5", 3), "f") == "1230"
    assert format(round_sig("2.345", 3), "f") == "2.35"
    assert format(round_sig("0.0012345", 3), "f") == "0.00123"


def test_round_places_is_half_away_from_zero():
    assert format(round_places("-0.125", 2), "f") == "-0.13"


def test_to_sci_keeps_digits():
    assert to_sci(round_sig("1234.5", 3)) == "1.23e3"


def test_calc_multiplication_uses_fewest_sig_figs():
    q = calc("12.30 * 0.5")
    assert q.value == Decimal("6")
    assert q.sig == 1


def test_calc_addition_uses_fewest_decimal_places():
    assert calc("12.3 + 4.56").value == Decimal("16.9")


def test_calc_whole_number_literals_are_exact():
    assert format(calc("2 * 3.40").value, "f") == "6.80"


def test_calc_rejects_calls_and_fractional_exponents():
    with pytest.raises(ValueError):
        calc("__import__('os')")
    with pytest.raises(ValueError):
        calc("2 ** 0.5")


def test_stats_mean_standard_error_and_sd():
    report = stats_report(["12.31", "12.29", "12.35"])
    assert report["n"] == 3
    assert report["mean"] == "12.317"
    assert report["standard_error"] == "0.018"
    assert report["standard_deviation"] == "0.031"


def test_stats_needs_two_values():
    with pytest.raises(ValueError):
        stats_report(["1.0"])


def test_propagate_density_quotient():
    rows = _rows("mass = 12.34 +- 0.02\nvolume = 4.00 +- 0.05\nd = mass / volume\n")
    assert _pair("mass = 12.34 +- 0.02\nvolume = 4.00 +- 0.05\nd = mass / volume\n") == ("3.09", "0.04")
    assert rows["d"].v == pytest.approx(3.085)


def test_propagate_addition_adds_uncertainties_in_quadrature():
    text = "a = 1.00 +- 0.03\nb = 2.00 +- 0.04\ns = a + b\n"
    assert _pair(text) == ("3.00", "0.05")


def test_propagate_integer_power():
    text = "x = 2.0 +- 0.1\ny = x ** 2\n"
    assert _pair(text) == ("4.0", "0.4")


def test_literals_in_formulas_are_exact():
    text = "x = 5.0 +- 0.5\ny = x * 2\n"
    assert _pair(text) == ("10.0", "1.0")


def test_worksheet_reports_unknown_name_with_line_number():
    _, problems = parse_worksheet("a = 1.0\nb = a + nope\n")
    assert [p.line for p in problems] == [2]
    assert "unknown name 'nope'" in problems[0].message


def test_worksheet_rejects_self_reference_and_duplicates():
    _, problems = parse_worksheet("q = q + 1\nr = 2\nr = 3\n")
    assert [p.line for p in problems] == [1, 3]


def test_worksheet_rejects_negative_uncertainty():
    _, problems = parse_worksheet("d = 1.0 +- -0.1\n")
    assert problems[0].line == 1
    assert "negative" in problems[0].message


def test_division_by_zero_is_a_worksheet_error():
    items, _ = parse_worksheet("a = 1.0 +- 0.1\nz = a / 0\n")
    with pytest.raises(WorksheetError) as info:
        propagate_items(items)
    assert info.value.line == 2


def test_check_gate_fails_when_relative_uncertainty_is_too_big(tmp_path, capsys):
    path = tmp_path / "w.sf"
    path.write_text("mass = 12.34 +- 0.02\nvolume = 4.00 +- 0.05\nd = mass / volume\n", encoding="utf-8")
    assert main(["check", str(path), "--max-rel-unc", "0.001"]) == 1
    assert "exceeds limit" in capsys.readouterr().out


def test_check_passes_on_the_example_worksheet(capsys):
    assert main(["check", str(EXAMPLES / "density.sf"), "--max-rel-unc", "0.02"]) == 0
    assert "ok: 4 value(s) checked" in capsys.readouterr().out


def test_check_reports_line_numbered_errors_in_bad_example(capsys):
    assert main(["check", str(EXAMPLES / "bad.sf")]) == 1
    out = capsys.readouterr().out
    assert "bad.sf:3:" in out
    assert "bad.sf:4:" in out
    assert "unknown name 'hieght'" in out
    assert "exponent must be an integer literal" in out
    assert "already defined" in out


def test_propagate_json_output(tmp_path, capsys):
    path = tmp_path / "w.sf"
    path.write_text("mass = 12.34 +- 0.02\nvolume = 4.00 +- 0.05\nd = mass / volume\n", encoding="utf-8")
    assert main(["propagate", str(path), "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data[-1]["name"] == "d"
    assert data[-1]["value"] == "3.09"
    assert data[-1]["uncertainty"] == "0.04"


def test_count_command_prints_result(capsys):
    assert main(["count", "12.30", "--explain"]) == 0
    out = capsys.readouterr().out
    assert "12.30: 4 significant figures" in out
    assert "trailing zeros" in out


def test_round_command_scientific_style(capsys):
    assert main(["round", "1234.5", "--sig", "3", "--style", "sci"]) == 0
    assert capsys.readouterr().out.strip() == "1.23e3"


def test_bad_input_returns_exit_code_two(capsys):
    assert main(["count", "twelve"]) == 2
    assert "not a plain decimal number" in capsys.readouterr().err


def test_module_exposes_cli_entry_point():
    assert callable(sigfig.main)
