"""User administration response examples must not prevent application startup."""

import ast
import json
from pathlib import Path


def test_user_settings_response_examples_are_json_values():
    source = Path(__file__).parents[2] / "langboard/routes/settings/UserSettingsApi.py"
    tree = ast.parse(source.read_text())
    checked = []
    for function in tree.body:
        if not isinstance(function, ast.FunctionDef):
            continue
        for decorator in function.decorator_list:
            if not isinstance(decorator, ast.Call):
                continue
            for keyword in decorator.keywords:
                if keyword.arg != "responses":
                    continue
                for call in ast.walk(keyword.value):
                    if isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute) and call.func.attr == "suc":
                        # Direct literal examples are passed through OpenApiSchema.
                        # A Python class in these examples breaks FastAPI's encoder.
                        if call.args and isinstance(call.args[0], ast.Dict):
                            json.dumps(ast.literal_eval(call.args[0]))
                            checked.append(function.name)
    assert {
        "get_users_page_in_settings",
        "get_user_business_activity_in_settings",
        "get_user_sign_in_history_in_settings",
        "get_user_effective_permissions_in_settings",
        "get_user_in_settings",
    }.issubset(checked)
