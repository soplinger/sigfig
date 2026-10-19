#!/usr/bin/env python3
"""sigfig: significant figures, exact rounding and uncertainty propagation.

Counts significant figures, applies the addition and multiplication rules,
and propagates measurement uncertainty through formulas written in plain
text lab worksheets. Standard library only; no network access.
"""

import argparse
import ast
import json
import math
import re
import statistics
import sys
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import NamedTuple

NUM = r"[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?"
NUM_RE = re.compile(NUM)
MEAS_RE = re.compile(rf"(?P<v>{NUM})(?:\s*(?:\+-|\+/-|±)\s*(?P<u>{NUM}))?")
ASSIGN_RE = re.compile(r"([A-Za-z_]\w*)\s*=\s*(.*)")


class Quantity(NamedTuple):
    """A value for sig-fig arithmetic. sig/places of None mean exact."""

    value: Decimal
    sig: int | None
    places: int | None


class Meas(NamedTuple):
    """A value with a first-order standard uncertainty (floats)."""

    v: float
    u: float


class Item(NamedTuple):
    line: int
    name: str
    tree: ast.expr | None
    meas: Meas | None
    explicit: bool


class Row(NamedTuple):
    line: int
    name: str
    v: float
    u: float


class Problem(NamedTuple):
    line: int
    col: int
    severity: str
    message: str


class WorksheetError(Exception):
    def __init__(self, line, message):
        super().__init__(f"line {line}: {message}")
        self.line = line
        self.message = message


# ---------------------------------------------------------------- numbers

def parse_number(text):
    """Return the stripped text if it is a plain decimal number, else raise."""
    s = text.strip()
    if not NUM_RE.fullmatch(s):
        raise ValueError(f"not a plain decimal number: {text!r}")
    return s


def count_sig_figs(text):
    """Return (count, ambiguous). Zero counts as one significant figure.

    Trailing zeros without a decimal point are not counted and are flagged
    as ambiguous, because "100" could mean one, two or three figures.
    """
    s = parse_number(text).lstrip("+-")
    mantissa = re.split(r"[eE]", s)[0]
    digits = mantissa.replace(".", "")
    stripped = digits.lstrip("0")
    if not stripped:
        return 1, False
    if "." in mantissa:
        return len(stripped), False
    kept = stripped.rstrip("0")
    return len(kept), len(kept) != len(stripped)


def explain_sig_figs(text):
    """Return human-readable notes explaining the significant-figure count."""
    s = parse_number(text).lstrip("+-")
    mantissa = re.split(r"[eE]", s)[0]
    digits = mantissa.replace(".", "")
    notes = []
    if re.search(r"[eE]", s):
        notes.append("the power-of-ten part does not add significant figures")
    lead = len(digits) - len(digits.lstrip("0"))
    if lead and digits.strip("0"):
        notes.append(f"{lead} leading zero(s) are placeholders and do not count")
    count, ambiguous = count_sig_figs(text)
    if not digits.strip("0"):
        notes.append("the value is zero; it is counted as 1 significant figure")
    elif "." in mantissa:
        notes.append("every digit from the first non-zero digit on counts, "
                     "including trailing zeros after the decimal point")
    elif ambiguous:
        notes.append("trailing zeros without a decimal point are ambiguous; "
                     "write scientific notation (e.g. 1.20e2) to make the count clear")
    notes.append(f"result: {count} significant figure(s)")
    return notes


def round_sig(value, sig):
    """Round to `sig` significant figures, half-up, keeping trailing zeros."""
    if sig < 1:
        raise ValueError("significant figures must be at least 1")
    d = Decimal(value)
    if d == 0:
        return Decimal(0).quantize(Decimal(1).scaleb(1 - sig))
    exp = d.adjusted() - sig + 1
    r = d.quantize(Decimal(1).scaleb(exp), rounding=ROUND_HALF_UP)
    if r.adjusted() > d.adjusted():  # carried into a new digit, e.g. 9.96 -> 10.0
        r = d.quantize(Decimal(1).scaleb(exp + 1), rounding=ROUND_HALF_UP)
    return r


def round_places(value, places):
    """Round to a fixed number of decimal places, half-up."""
    return Decimal(value).quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)


def to_sci(d):
    """Format a Decimal as scientific notation, e.g. 1.23e3, keeping its digits."""
    t = d.as_tuple()
    sign = "-" if t.sign else ""
    if d == 0:
        mantissa = "0" + ("." + "0" * -t.exponent if t.exponent < 0 else "")
        return f"{sign}{mantissa}e0"
    digits = "".join(str(x) for x in t.digits)
    exp = t.exponent + len(digits) - 1
    mantissa = digits[0] + ("." + digits[1:] if len(digits) > 1 else "")
    return f"{sign}{mantissa}e{exp}"


def render(d, style="plain"):
    return to_sci(d) if style == "sci" else format(d, "f")


def _sig_of(d):
    stripped = "".join(str(x) for x in d.as_tuple().digits).lstrip("0")
    return max(1, len(stripped))


def _places_of(d):
    return max(0, -d.as_tuple().exponent)


def _literal_places(text):
    return _places_of(Decimal(parse_number(text)))


def _dec(x):
    """Convert a float to a Decimal with 12 significant digits, hiding float noise."""
    return Decimal(f"{x:.12e}")


def round_uncertainty(u):
    """Round an uncertainty to 1 significant figure, or 2 when its leading digit is 1."""
    d = _dec(abs(u))
    if d == 0:
        return Decimal(0)
    lead = int(d.scaleb(-d.adjusted()))
    return round_sig(d, 2 if lead == 1 else 1)


def display_pair(v, u):
    """Round a value and its uncertainty consistently; returns two Decimals."""
    value = _dec(v)
    if u == 0:
        return round_sig(value, 12).normalize(), Decimal(0)
    ur = round_uncertainty(u)
    vr = value.quantize(Decimal(1).scaleb(ur.as_tuple().exponent), rounding=ROUND_HALF_UP)
    return vr, ur


# ------------------------------------------------- sig-fig arithmetic (calc)

def _q_additive(a, b, raw):
    """Addition rule: round to the fewest decimal places of the operands."""
    places = [q.places for q in (a, b) if q.places is not None]
    if not places:
        return Quantity(raw, None, None)
    p = min(places)
    v = raw.quantize(Decimal(1).scaleb(-p), rounding=ROUND_HALF_UP)
    return Quantity(v, _sig_of(v), p)


def _q_multiplicative(a, b, raw):
    """Multiplication rule: round to the fewest significant figures of the operands."""
    sigs = [q.sig for q in (a, b) if q.sig is not None]
    if not sigs:
        return Quantity(raw, None, None)
    s = min(sigs)
    v = round_sig(raw, s)
    return Quantity(v, s, _places_of(v))


def _q_pow(q, n):
    raw = Decimal(1) if n == 0 else q.value ** n
    if q.sig is None:
        return Quantity(raw, None, None)
    v = round_sig(raw, q.sig)
    return Quantity(v, q.sig, _places_of(v))


def _calc_eval(node, src):
    if isinstance(node, ast.Constant):
        text = parse_number(ast.get_source_segment(src, node))
        if text.isdigit():  # whole-number literals are exact counts
            return Quantity(Decimal(text), None, None)
        return Quantity(Decimal(text), count_sig_figs(text)[0], _literal_places(text))
    if isinstance(node, ast.UnaryOp):
        q = _calc_eval(node.operand, src)
        if isinstance(node.op, ast.USub):
            return Quantity(-q.value, q.sig, q.places)
        return q
    left = _calc_eval(node.left, src)
    if isinstance(node.op, ast.Pow):
        return _q_pow(left, _int_literal(node.right))
    right = _calc_eval(node.right, src)
    if isinstance(node.op, ast.Add):
        return _q_additive(left, right, left.value + right.value)
    if isinstance(node.op, ast.Sub):
        return _q_additive(left, right, left.value - right.value)
    if isinstance(node.op, ast.Mult):
        return _q_multiplicative(left, right, left.value * right.value)
    return _q_multiplicative(left, right, left.value / right.value)


def calc(expression):
    """Evaluate one expression under significant-figure rules."""
    src = expression.strip()
    try:
        tree = ast.parse(src, mode="eval").body
    except SyntaxError as exc:
        raise ValueError(f"cannot parse expression: {exc.msg}") from None
    bad = _check_tree(tree, set())
    if bad:
        node, msg = bad[0]
        raise ValueError(f"column {node.col_offset + 1}: {msg}")
    return _calc_eval(tree, src)


# ------------------------------------------------------- formula validation

def _int_literal(node):
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        inner = _int_literal(node.operand)
        if inner is None:
            return None
        return -inner if isinstance(node.op, ast.USub) else inner
    if isinstance(node, ast.Constant) and type(node.value) is int:
        return node.value
    return None


def _check_tree(tree, known):
    """Return (node, message) for every construct a formula may not use."""
    found = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Expression, ast.operator, ast.unaryop, ast.expr_context)):
            continue
        if isinstance(node, ast.Constant):
            if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
                found.append((node, "only plain numbers are allowed here"))
        elif isinstance(node, ast.Name):
            if node.id not in known:
                found.append((node, f"unknown name '{node.id}'"))
        elif isinstance(node, ast.UnaryOp):
            if not isinstance(node.op, (ast.USub, ast.UAdd)):
                found.append((node, "operator not allowed"))
        elif isinstance(node, ast.BinOp):
            if not isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow)):
                found.append((node, "operator not allowed"))
            elif isinstance(node.op, ast.Pow) and _int_literal(node.right) is None:
                found.append((node, "exponent must be an integer literal"))
        else:
            found.append((node, f"'{type(node).__name__}' is not allowed in formulas"))
    return sorted(found, key=lambda pair: pair[0].col_offset)


# ----------------------------------------------------------------- worksheet

def parse_worksheet(text):
    """Parse a worksheet into (items, problems). Lines are 'name = value' or 'name = formula'.

    A value is 'number' or 'number +- uncertainty'. Without an explicit
    uncertainty, the uncertainty is half of the last written digit.
    Literals inside formulas are exact. '#' starts a comment.
    """
    items, problems, known = [], [], set()
    for lineno, raw in enumerate(text.splitlines(), start=1):
        code = raw.split("#", 1)[0]
        if not code.strip():
            continue
        match = ASSIGN_RE.fullmatch(code.strip())
        if match is None:
            problems.append(Problem(lineno, 1, "error", "expected 'name = value' or 'name = formula'"))
            continue
        name, rhs = match.group(1), match.group(2).strip()
        eq = code.index("=")
        after = code[eq + 1:]
        rhs_col = eq + 2 + len(after) - len(after.lstrip())
        if name in known:
            problems.append(Problem(lineno, 1, "error", f"'{name}' is already defined"))
            continue
        prior = set(known)
        known.add(name)
        if not rhs:
            problems.append(Problem(lineno, rhs_col, "error", "missing value after '='"))
            continue
        meas = MEAS_RE.fullmatch(rhs)
        if meas is not None:
            value = Decimal(meas.group("v"))
            if meas.group("u") is None:
                unc, explicit = Decimal("0.5").scaleb(value.as_tuple().exponent), False
            else:
                unc, explicit = Decimal(meas.group("u")), True
                if unc < 0:
                    problems.append(Problem(lineno, rhs_col, "error", "uncertainty must not be negative"))
                    continue
            items.append(Item(lineno, name, None, Meas(float(value), float(unc)), explicit))
            continue
        try:
            tree = ast.parse(rhs, mode="eval").body
        except SyntaxError as exc:
            problems.append(Problem(lineno, rhs_col + (exc.offset or 1) - 1, "error",
                                    f"cannot parse '{rhs}': {exc.msg}"))
            continue
        bad = _check_tree(tree, prior)
        if bad:
            problems.extend(Problem(lineno, rhs_col + node.col_offset, "error", msg)
                            for node, msg in bad)
            continue
        items.append(Item(lineno, name, tree, None, False))
    return items, problems


def _prop(node, env):
    """First-order uncertainty propagation through one formula node."""
    if isinstance(node, ast.Constant):
        return Meas(float(node.value), 0.0)
    if isinstance(node, ast.Name):
        return env[node.id]
    if isinstance(node, ast.UnaryOp):
        m = _prop(node.operand, env)
        return Meas(-m.v if isinstance(node.op, ast.USub) else m.v, m.u)
    a = _prop(node.left, env)
    if isinstance(node.op, ast.Pow):
        n = _int_literal(node.right)
        if n == 0:
            return Meas(1.0, 0.0)
        return Meas(a.v ** n, abs(n * a.v ** (n - 1)) * a.u)
    b = _prop(node.right, env)
    if isinstance(node.op, ast.Add):
        return Meas(a.v + b.v, math.hypot(a.u, b.u))
    if isinstance(node.op, ast.Sub):
        return Meas(a.v - b.v, math.hypot(a.u, b.u))
    if isinstance(node.op, ast.Mult):
        return Meas(a.v * b.v, math.hypot(b.v * a.u, a.v * b.u))
    if b.v == 0:
        raise ZeroDivisionError("division by zero")
    return Meas(a.v / b.v, math.hypot(a.u / b.v, a.v * b.u / b.v ** 2))


def propagate_items(items):
    env, rows = {}, []
    for item in items:
        try:
            meas = item.meas if item.tree is None else _prop(item.tree, env)
        except ArithmeticError as exc:
            raise WorksheetError(item.line, str(exc) or "arithmetic error") from None
        env[item.name] = meas
        rows.append(Row(item.line, item.name, meas.v, meas.u))
    return rows


def stats_report(values):
    """Mean with standard error and sample standard deviation of repeated readings."""
    xs = [float(parse_number(v)) for v in values]
    if len(xs) < 2:
        raise ValueError("need at least two values for a standard deviation")
    mean = statistics.fmean(xs)
    sd = statistics.stdev(xs)
    se = sd / math.sqrt(len(xs))
    vm, ue = display_pair(mean, se)
    return {
        "n": len(xs),
        "mean": format(vm, "f"),
        "standard_deviation": format(round_sig(_dec(sd), 2), "f"),
        "standard_error": format(ue, "f"),
    }


# ----------------------------------------------------------------------- CLI

def _read(path):
    return Path(path).read_text(encoding="utf-8")


def _row_text(row):
    value, unc = display_pair(row.v, row.u)
    rel = f"{abs(row.u / row.v) * 100:.2f}%" if row.v else "n/a"
    return f"{row.line:>4}  {row.name:<12} = {format(value, 'f')} ± {format(unc, 'f')}  (rel {rel})"


def cmd_count(args):
    count, _ = count_sig_figs(args.number)
    plural = "" if count == 1 else "s"
    print(f"{args.number.strip()}: {count} significant figure{plural}")
    if args.explain:
        for note in explain_sig_figs(args.number):
            print(f"  - {note}")
    return 0


def cmd_round(args):
    d = Decimal(parse_number(args.number))
    result = round_sig(d, args.sig) if args.sig is not None else round_places(d, args.places)
    print(render(result, args.style))
    return 0


def cmd_calc(args):
    q = calc(args.expression)
    sig = "exact" if q.sig is None else f"{q.sig} sig figs"
    places = "exact" if q.places is None else f"{q.places} decimal places"
    print(format(q.value, "f"))
    print(f"  ({sig}, {places})")
    return 0


def cmd_stats(args):
    report = stats_report(args.values)
    print(f"n = {report['n']}")
    print(f"mean = {report['mean']} ± {report['standard_error']} (standard error)")
    print(f"sample standard deviation = {report['standard_deviation']}")
    return 0


def cmd_propagate(args):
    items, problems = parse_worksheet(_read(args.file))
    if problems:
        for p in problems:
            print(f"{args.file}:{p.line}:{p.col}: {p.severity}: {p.message}")
        return 1
    try:
        rows = propagate_items(items)
    except WorksheetError as exc:
        print(f"{args.file}:{exc.line}: error: {exc.message}")
        return 1
    if args.json:
        out = []
        for row in rows:
            value, unc = display_pair(row.v, row.u)
            out.append({
                "line": row.line,
                "name": row.name,
                "value": format(value, "f"),
                "uncertainty": format(unc, "f"),
                "relative_percent": abs(row.u / row.v) * 100 if row.v else None,
            })
        print(json.dumps(out, indent=2))
    else:
        for row in rows:
            print(_row_text(row))
    return 0


def cmd_check(args):
    items, problems = parse_worksheet(_read(args.file))
    findings = list(problems)
    for item in items:
        if item.tree is None and not item.explicit:
            findings.append(Problem(item.line, 1, "warning",
                                    f"'{item.name}' has no ± part; uncertainty taken as half the last digit"))
    if not any(p.severity == "error" for p in findings):
        try:
            rows = propagate_items(items)
        except WorksheetError as exc:
            findings.append(Problem(exc.line, 1, "error", exc.message))
            rows = []
        if args.max_rel_unc is not None:
            for row in rows:
                if row.v and abs(row.u / row.v) > args.max_rel_unc:
                    rel = abs(row.u / row.v) * 100
                    findings.append(Problem(row.line, 1, "error",
                                            f"'{row.name}' relative uncertainty {rel:.2f}% "
                                            f"exceeds limit {args.max_rel_unc * 100:.2f}%"))
    findings.sort(key=lambda p: (p.line, p.col))
    for p in findings:
        print(f"{args.file}:{p.line}:{p.col}: {p.severity}: {p.message}")
    errors = sum(p.severity == "error" for p in findings)
    warnings = len(findings) - errors
    if not findings:
        print(f"ok: {len(items)} value(s) checked")
        return 0
    print(f"{errors} error(s), {warnings} warning(s) in {len(items)} value(s)")
    return 1 if errors or (args.strict and warnings) else 0


def build_parser():
    p = argparse.ArgumentParser(
        prog="sigfig",
        description="Significant figures, exact rounding and uncertainty propagation.")
    sub = p.add_subparsers(dest="command", required=True)

    c = sub.add_parser("count", help="count significant figures in a number")
    c.add_argument("number")
    c.add_argument("--explain", action="store_true", help="show the reasoning")
    c.set_defaults(handler=cmd_count)

    r = sub.add_parser("round", help="round to significant figures or decimal places")
    r.add_argument("number")
    group = r.add_mutually_exclusive_group(required=True)
    group.add_argument("--sig", type=int, help="significant figures to keep")
    group.add_argument("--places", type=int, help="decimal places to keep")
    r.add_argument("--style", choices=["plain", "sci"], default="plain")
    r.set_defaults(handler=cmd_round)

    k = sub.add_parser("calc", help="evaluate an expression under sig-fig rules")
    k.add_argument("expression", help="e.g. \"12.30 * 0.5\"; whole numbers are exact")
    k.set_defaults(handler=cmd_calc)

    s = sub.add_parser("stats", help="mean, standard error and SD of repeated readings")
    s.add_argument("values", nargs="+")
    s.set_defaults(handler=cmd_stats)

    pr = sub.add_parser("propagate", help="propagate uncertainty through a worksheet")
    pr.add_argument("file")
    pr.add_argument("--json", action="store_true", help="print JSON instead of text")
    pr.set_defaults(handler=cmd_propagate)

    ck = sub.add_parser("check", help="lint a worksheet and optionally gate uncertainty")
    ck.add_argument("file")
    ck.add_argument("--max-rel-unc", type=float, metavar="FRACTION",
                    help="fail if any value's relative uncertainty exceeds this (e.g. 0.02)")
    ck.add_argument("--strict", action="store_true", help="treat warnings as failures")
    ck.set_defaults(handler=cmd_check)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return args.handler(args)
    except (ValueError, ArithmeticError, OSError) as exc:
        print(f"sigfig: error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
