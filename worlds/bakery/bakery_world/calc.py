import ast
import math
import operator
import statistics

from bakery_world.llm import Tool, ToolError

MAX_EXPRESSION_CHARS = 4000
MAX_POWER_BITS = 4096

Number = int | float


class CalcError(Exception):
    pass


def pct_change(a: Number, b: Number) -> float:
    if a == 0:
        raise CalcError("pct_change needs a nonzero starting value")
    return (b - a) / abs(a) * 100


def linreg(xs: list, ys: list) -> dict[str, float]:
    if len(xs) != len(ys) or len(xs) < 2:
        raise CalcError("linreg needs two lists of the same length, at least 2 points")
    slope, intercept = statistics.linear_regression(xs, ys)
    r = statistics.correlation(xs, ys) if len(set(ys)) > 1 else 1.0
    return {"slope": slope, "intercept": intercept, "r_squared": r * r}


FUNCTIONS = {
    "sum": sum,
    "min": min,
    "max": max,
    "abs": abs,
    "round": round,
    "mean": statistics.fmean,
    "median": statistics.median,
    "stdev": statistics.stdev,
    "sqrt": math.sqrt,
    "log": math.log,
    "exp": math.exp,
    "pct_change": pct_change,
    "linreg": linreg,
}

BINARY = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}


def _number(value: object) -> Number:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise CalcError("arithmetic works on numbers only")
    return value


def _power(base: Number, exponent: Number) -> Number:
    if abs(exponent) > MAX_POWER_BITS:
        raise CalcError("exponent too large")
    if isinstance(base, int) and isinstance(exponent, int) and exponent > 0:
        if abs(base).bit_length() * exponent > MAX_POWER_BITS:
            raise CalcError("result too large")
    return base**exponent


def _eval(node: ast.AST) -> object:
    match node:
        case ast.Expression(body=body):
            return _eval(body)
        case ast.Constant(value=value):
            return _number(value)
        case ast.UnaryOp(op=ast.USub(), operand=operand):
            return -_number(_eval(operand))
        case ast.UnaryOp(op=ast.UAdd(), operand=operand):
            return _number(_eval(operand))
        case ast.BinOp(left=left, op=op, right=right) if type(op) in BINARY:
            a, b = _number(_eval(left)), _number(_eval(right))
            return _power(a, b) if isinstance(op, ast.Pow) else BINARY[type(op)](a, b)
        case ast.List(elts=elts) | ast.Tuple(elts=elts):
            return [_eval(e) for e in elts]
        case ast.Call(func=ast.Name(id=name), args=args, keywords=[]) if name in FUNCTIONS:
            return FUNCTIONS[name](*(_eval(a) for a in args))
        case ast.Call():
            raise CalcError(f"only these functions are allowed: {', '.join(FUNCTIONS)}")
        case ast.Name(id=name):
            raise CalcError(f"unknown name {name!r}; write the numbers in")
        case _:
            raise CalcError(f"{type(node).__name__} is not allowed")


def _show(value: object) -> str:
    if isinstance(value, float):
        return format(value, ".10g")
    if isinstance(value, list):
        return "[" + ", ".join(_show(v) for v in value) + "]"
    if isinstance(value, dict):
        return ", ".join(f"{k} = {_show(v)}" for k, v in value.items())
    return str(value)


def calculate(expression: str) -> str:
    if len(expression) > MAX_EXPRESSION_CHARS:
        return f"error: keep expressions under {MAX_EXPRESSION_CHARS} characters"
    try:
        return _show(_eval(ast.parse(expression.strip(), mode="eval")))
    except SyntaxError as e:
        return f"error: not a valid expression ({e.msg})"
    except CalcError as e:
        return f"error: {e}"
    except (ArithmeticError, ValueError, TypeError, RecursionError) as e:
        return f"error: {e}"


def calculator_tool() -> Tool:
    def run(args: dict) -> str:
        expression = args.get("expression")
        if not isinstance(expression, str):
            raise ToolError("error: expression must be text")
        return calculate(expression)

    return Tool(
        "calculate",
        "Evaluate arithmetic exactly: numbers, + - * / // % **, parentheses, lists and "
        f"{', '.join(FUNCTIONS)}. pct_change(a, b) is the % change from a to b; "
        "linreg(xs, ys) gives slope, intercept and r_squared.",
        {"expression": {"type": "string", "description": "e.g. mean([412, 398, 455])"}},
        run,
    )
