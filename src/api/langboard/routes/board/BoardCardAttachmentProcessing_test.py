"""Execute the route body to verify safe remediation without exposing provider errors."""

import ast
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock


class AttachmentProcessingErrorTest(unittest.TestCase):
    def handler(self, failure, *, denied=False):
        path = Path(__file__).with_name("BoardCardAttachmentApi.py")
        function = next(
            node
            for node in ast.parse(path.read_text()).body
            if isinstance(node, ast.FunctionDef) and node.name == "process_card_attachment_document"
        )
        function.decorator_list = []
        function.returns = None
        function.args.defaults = []
        for argument in function.args.args:
            argument.annotation = None
        calls = Mock(side_effect=failure)
        access = Mock(side_effect=PermissionError("denied") if denied else None)
        context = {
            "ApiErrorCode": SimpleNamespace(VA0000="VA0000", VA3007="VA3007", VA3008="VA3008", NF2009="NF2009"),
            "ApiException": SimpleNamespace(
                BadRequest_400=lambda code: ValueError(code), NotFound_404=lambda code: LookupError(code)
            ),
            "require_visible_card": access,
            "require_card_child": Mock(),
            "CardAttachment": object,
            "JsonResponse": lambda **kwargs: kwargs,
        }
        exec(compile(ast.Module(body=[function], type_ignores=[]), str(path), "exec"), context)
        service = SimpleNamespace(card_attachment=SimpleNamespace(request_document_embedding=calls))
        return lambda: context[function.name](
            "board", "card", object(), "attachment", SimpleNamespace(mode="embedding"), object(), service
        ), calls

    def test_missing_profile_and_transcription_have_distinct_safe_codes(self):
        for message, code in (
            ("Configure an embedding provider before indexing attachments", "VA3007"),
            ("Process this attachment before requesting embeddings", "VA3008"),
        ):
            with self.subTest(code=code):
                handler, calls = self.handler(ValueError(message))
                with self.assertRaisesRegex(ValueError, code):
                    handler()
                calls.assert_called_once()

    def test_arbitrary_provider_error_is_never_returned(self):
        handler, _ = self.handler(ValueError("credential-and-document-content-must-remain-private"))
        with self.assertRaisesRegex(ValueError, "^VA0000$"):
            handler()

    def test_current_card_authority_is_checked_before_processing(self):
        handler, calls = self.handler(ValueError("unused"), denied=True)
        with self.assertRaises(PermissionError):
            handler()
        calls.assert_not_called()


if __name__ == "__main__":
    unittest.main()
