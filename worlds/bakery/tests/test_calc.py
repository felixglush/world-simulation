import pytest

from bakery_world.calc import calculate


@pytest.mark.parametrize(
    "expression, answer",
    [
        ("(3 + 4) * 2 - 10 / 4", "11.5"),
        ("7 // 2 + 7 % 2 + 2 ** 10", "1028"),
        ("-abs(-3) + round(2.25, 1)", "-0.8"),
        ("mean([2, 4, 9])", "5"),
        ("median([5, 1, 9, 3])", "4"),
        ("stdev([2, 4, 4, 4, 5, 5, 7, 9])", "2.138089935"),
        ("pct_change(80, 100)", "25"),
        ("sum([1, 2, 3]) + max(4, 5) - min([6, 7])", "5"),
        ("sqrt(16) + log(exp(2))", "6"),
        ("linreg([1, 2, 3, 4], [5, 7, 9, 11])", "slope = 2, intercept = 3, r_squared = 1"),
        ("[1, 2 * 3]", "[1, 6]"),
    ],
)
def test_calculate_gets_the_right_answers(expression: str, answer: str) -> None:
    assert calculate(expression) == answer


@pytest.mark.parametrize(
    "expression",
    [
        "__import__('os')",
        "(1).__class__",
        "9**999999",
        "x + 1",
        "open('records.sqlite')",
        "[n for n in [1, 2]]",
        "'a' * 3",
        "[1] * 10**9",
        "1 / 0",
        "mean([])",
        "1 +",
    ],
)
def test_calculate_refuses_anything_but_arithmetic(expression: str) -> None:
    assert calculate(expression).startswith("error: ")
