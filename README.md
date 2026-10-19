# sigfig

**Significant figures, exact rounding and uncertainty propagation for lab worksheets, offline.**

sigfig counts significant figures, applies the addition and multiplication rules, and propagates measurement uncertainty through formulas written in a plain-text worksheet. Every value comes out with an uncertainty rounded the standard way, and every problem in a worksheet is reported with its line and column. It uses only the Python 3 standard library and makes no network calls.

## Features

- **Significant-figure counting** with the standard rules (leading zeros don't count, trailing zeros after a decimal point do, trailing zeros without one are flagged as ambiguous).
- **`--explain` mode** that shows why a number has the count it has.
- **Rounding to significant figures or decimal places**, half-up, keeping trailing zeros, with carry handling (`9.96` to 2 s.f. gives `10`).
- **Scientific-notation output** (`round --style sci`), e.g. `1.23e3`.
- **Sig-fig arithmetic** with `calc`: addition rounds to the fewest decimal places, multiplication and division to the fewest significant figures, and whole-number literals are treated as exact counts.
- **Repeated-measurement statistics** with `stats`: mean, standard error, and sample standard deviation, with the mean rounded to match its uncertainty.
- **Worksheet uncertainty propagation** with `propagate`: first-order (quadrature) propagation through `+ - * /` and integer powers.
- **Standard uncertainty rounding**: one significant figure, or two when the leading digit is 1, with the value rounded to the same place.
- **Line-numbered worksheet errors**: unknown names, duplicate definitions, syntax errors, forbidden constructs and negative uncertainties, each with `file:line:col`.
- **Lint warnings** for values written without a `±` part, with `--strict` to make them fail.
- **CI gate** with `check --max-rel-unc FRACTION` that fails when any value's relative uncertainty is too large.
- **JSON output** for `propagate --json`, for scripts and dashboards.
- **Safe formula language**: only numbers, names defined on earlier lines, `+ - * /`, `**` with an integer exponent and unary signs are accepted. Calls, attributes and imports are rejected, so worksheets can't run code.

## Quick start

Save this as `density.sf` (or use the copy in `examples/`):

```text
# Density of a rock sample, measured with a balance and a graduated cylinder
mass = 12.34 +- 0.02
volume = 4.00 +- 0.05

# Derived values: uncertainties propagate automatically
density = mass / volume
column = density * 2.5
```

Run it:

```console
$ python3 sigfig.py propagate examples/density.sf
   2  mass         = 12.34 ± 0.02  (rel 0.16%)
   3  volume       = 4.00 ± 0.05  (rel 1.25%)
   6  density      = 3.09 ± 0.04  (rel 1.26%)
   7  column       = 7.7 ± 0.1  (rel 1.26%)
```

Check it as a CI gate, failing if any value is more than 2% uncertain:

```console
$ python3 sigfig.py check examples/density.sf --max-rel-unc 0.02
ok: 4 value(s) checked
```

Count and round on the command line:

```console
$ python3 sigfig.py count 12.30 --explain
12.30: 4 significant figures
  - every digit from the first non-zero digit on counts, including trailing zeros after the decimal point
  - result: 4 significant figure(s)

$ python3 sigfig.py round 1234.5 --sig 3 --style sci
1.23e3

$ python3 sigfig.py calc "12.30 * 0.5"
6
  (1 sig figs, 0 decimal places)
```

## Requirements

- Python 3.10 or newer (uses `int | None` annotations and `Decimal`).
- Nothing to install. The tool uses only the standard library.
- pytest is needed only to run the test suite.

## How to run

```console
python3 sigfig.py count NUMBER [--explain]
python3 sigfig.py round NUMBER (--sig N | --places N) [--style plain|sci]
python3 sigfig.py calc "EXPRESSION"
python3 sigfig.py stats VALUE VALUE [VALUE ...]
python3 sigfig.py propagate WORKSHEET [--json]
python3 sigfig.py check WORKSHEET [--max-rel-unc FRACTION] [--strict]
```

Exit codes: `0` success, `1` the worksheet has errors (or warnings under `--strict`, or a relative-uncertainty gate failure), `2` bad command-line input.

### Worksheet format

- One value per line: `name = number` or `name = number +- uncertainty` (`±` also works).
- Derived values: `name = formula`, using names defined on earlier lines. Operators: `+ - * /` and `**` with an integer exponent. Numbers written inside a formula are exact.
- `#` starts a comment; blank lines are ignored.
- A plain number with no `±` gets an uncertainty of half of its last written digit. `check` warns about this, and `--strict` turns the warning into a failure.

## How to test

```console
python3 -m pytest -q tests
```

The tests in `tests/test_sigfig.py` cover the sig-fig rules, rounding carry and half-up behaviour, arithmetic with exact literals, uncertainty propagation against hand-worked values, the statistics report, worksheet errors with line numbers, the CI gate, JSON output and CLI exit codes.

## Notes on the rules

- Leading zeros are placeholders. Zero itself counts as one significant figure.
- Uncertainties are combined in quadrature (first-order propagation). Literals inside formulas have no uncertainty.
- Rounding is half-up (away from zero for negative numbers), as is usual in lab reports.
- Intermediate results in `calc` keep the figures the rules give them, so the answer for `12.30 * 0.5` is `6`, not `6.15`.

## Project layout

```text
sigfig.py              the tool (single module, command-line entry point)
examples/density.sf    a worked worksheet with derived values
examples/bad.sf        a deliberately broken worksheet for error reporting
tests/conftest.py      puts the project root on sys.path for pytest
tests/test_sigfig.py   test suite
```
