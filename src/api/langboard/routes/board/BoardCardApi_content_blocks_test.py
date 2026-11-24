"""Exercise the editor's HTTP envelope through the actual endpoint signature/body."""

import ast
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from fastapi import Body, Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient


class ContentBlockSaveTest(unittest.TestCase):
    def test_object_envelope_and_explicit_clear_require_valid_blocks(self):
        source = Path(__file__).with_name("BoardCardApi.py")
        endpoint = next(
            node
            for node in ast.parse(source.read_text()).body
            if isinstance(node, ast.FunctionDef) and node.name == "replace_card_content_blocks"
        )
        endpoint.decorator_list = []
        calls = []
        visible = []
        actor = object()
        service = SimpleNamespace(
            card_content_block=SimpleNamespace(replace_blocks=lambda *args: calls.append(args) or args[-1])
        )
        namespace = dict(
            Body=Body,
            Request=Request,
            User=Any,
            Bot=Any,
            Auth=SimpleNamespace(scope=lambda _: Depends(lambda: actor)),
            DomainService=SimpleNamespace(scope=lambda: Depends(lambda: service)),
            JsonResponse=JSONResponse,
            require_visible_card=lambda *args: visible.append(args),
        )
        exec(compile(ast.Module(body=[endpoint], type_ignores=[]), str(source), "exec"), namespace)
        app = FastAPI()
        app.put("/board/{project_uid}/card/{card_uid}/content-blocks")(namespace[endpoint.name])
        with TestClient(app) as client:
            path = "/board/project-a/card/card-a/content-blocks"
            blocks = [{"block_type": "text", "payload": {"content": "preserved"}}]
            for value in (blocks, []):
                response = client.put(path, json={"blocks": value})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json(), {"blocks": value})
                self.assertEqual(calls[-1], (actor, "project-a", "card-a", value))
            self.assertEqual(len(visible), 2)
            for invalid in ({}, {"blocks": None}, {"blocks": {}}, []):
                self.assertEqual(client.put(path, json=invalid).status_code, 422)
            self.assertEqual(len(calls), 2, "Invalid or missing blocks must never clear existing content")


if __name__ == "__main__":
    unittest.main()
