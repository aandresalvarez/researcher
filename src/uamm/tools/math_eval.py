import ast
import math
from typing import Any


_ALLOWED_FUNCS = {
    k: getattr(math, k)
    for k in (
        "ceil",
        "floor",
        "sqrt",
        "log",
        "log10",
        "exp",
        "sin",
        "cos",
        "tan",
        "asin",
        "acos",
        "atan",
    )
}


class _SafeEval(ast.NodeVisitor):
    def visit(self, node: ast.AST) -> Any:  # type: ignore[override]
        if isinstance(node, ast.Expression):
            return self.visit(node.body)
        if isinstance(node, ast.Constant):
            if isinstance(node.value, (int, float)):
                return node.value
            raise ValueError("only numbers allowed")
        if isinstance(node, ast.BinOp) and isinstance(
            node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow, ast.Mod)
        ):
            left = float(self.visit(node.left))
            right = float(self.visit(node.right))
            if isinstance(node.op, ast.Add):
                return left + right
            if isinstance(node.op, ast.Sub):
                return left - right
            if isinstance(node.op, ast.Mult):
                return left * right
            if isinstance(node.op, ast.Div):
                return left / right
            if isinstance(node.op, ast.Pow):
                return left**right
            if isinstance(node.op, ast.Mod):
                return left % right
            raise ValueError("op not allowed")
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            val = self.visit(node.operand)
            return +val if isinstance(node.op, ast.UAdd) else -val
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            name = node.func.id
            if name not in _ALLOWED_FUNCS:
                raise ValueError("function not allowed")
            args = [self.visit(a) for a in node.args]
            return _ALLOWED_FUNCS[name](*args)
        if isinstance(node, ast.Expr):
            return self.visit(node.value)
        raise ValueError("disallowed expression")


def math_eval(expr: str) -> float:
    tree = ast.parse(expr, mode="eval")
    return float(_SafeEval().visit(tree))
