"""Tiny safe expressions for business rules in spec.json.

Examples: "invoice.balance - payment.amount", "invoice.balance > 0 and invoice.balance < invoice.total".
Only names (entity.field), numbers, strings, + - * /, comparisons and and/or/not
are allowed. Expressions are parsed with `ast` and walked by our own evaluator;
Python's eval is never used.
"""

import ast
import operator

BINARY = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv}
COMPARE = {
    ast.Eq: operator.eq, ast.NotEq: operator.ne, ast.Lt: operator.lt,
    ast.LtE: operator.le, ast.Gt: operator.gt, ast.GtE: operator.ge,
}


class ExprError(ValueError):
    pass


def parse(text: str) -> ast.Expression:
    """Parse and check an expression. Raises ExprError for anything outside the whitelist."""
    try:
        tree = ast.parse(text, mode="eval")
    except SyntaxError as exc:
        raise ExprError(f"cannot parse {text!r}: {exc.msg}") from None
    for node in ast.walk(tree):
        allowed = (
            ast.Expression, ast.BinOp, ast.UnaryOp, ast.BoolOp, ast.Compare, ast.Name, ast.Attribute,
            ast.Constant, ast.Load, ast.And, ast.Or, ast.Not, ast.USub, *BINARY, *COMPARE,
        )
        if not isinstance(node, allowed):
            raise ExprError(f"{type(node).__name__} is not allowed in {text!r}")
        if isinstance(node, ast.Attribute) and not isinstance(node.value, ast.Name):
            raise ExprError(f"only entity.field names are allowed in {text!r}")
        if isinstance(node, ast.Constant) and not isinstance(node.value, (int, float, str)):
            raise ExprError(f"constant {node.value!r} is not allowed in {text!r}")
    return tree


def names(text: str) -> set[str]:
    """Every entity.field (or bare name) the expression reads."""
    nodes = list(ast.walk(parse(text)))
    bases = {id(n.value) for n in nodes if isinstance(n, ast.Attribute)}
    return {f"{n.value.id}.{n.attr}" for n in nodes if isinstance(n, ast.Attribute)} | {
        n.id for n in nodes if isinstance(n, ast.Name) and id(n) not in bases
    }


def evaluate(text: str, values: dict):
    """Evaluate with values like {"invoice": {"balance": 64000}, "payment": {"amount": 30000}}."""
    return _eval(parse(text).body, values)


def _eval(node, values):
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Attribute):
        try:
            return values[node.value.id][node.attr]
        except KeyError:
            raise ExprError(f"unknown name {node.value.id}.{node.attr}") from None
    if isinstance(node, ast.Name):
        if node.id not in values:
            raise ExprError(f"unknown name {node.id}")
        return values[node.id]
    if isinstance(node, ast.BinOp):
        return BINARY[type(node.op)](_eval(node.left, values), _eval(node.right, values))
    if isinstance(node, ast.UnaryOp):
        value = _eval(node.operand, values)
        return -value if isinstance(node.op, ast.USub) else not value
    if isinstance(node, ast.BoolOp):
        results = (_eval(v, values) for v in node.values)
        return all(results) if isinstance(node.op, ast.And) else any(results)
    if isinstance(node, ast.Compare):
        left = _eval(node.left, values)
        for op, right_node in zip(node.ops, node.comparators):
            right = _eval(right_node, values)
            if not COMPARE[type(op)](left, right):
                return False
            left = right
        return True
    raise ExprError(f"{type(node).__name__} is not allowed")
