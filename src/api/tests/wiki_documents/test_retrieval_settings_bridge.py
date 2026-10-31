"""Local production UI -> native HTTP/config/queue -> native splitter and SQLite proof.

Run explicitly with LANGBOARD_RUN_RETRIEVAL_BROWSER=1. Only transport delivery and
remote embeddings are replaced; no production handler or authorization is replaced.
"""

import asyncio
import json
import os
import subprocess
import threading
from hashlib import sha256
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import Mock
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langboard.middlewares.ApiAuthMiddleware import ApiAuthMiddleware
from langboard.middlewares.RoleMiddleware import RoleMiddleware
from langboard.routes.board import BoardCardAttachmentApi
from langboard.routes.settings import InternalBotSettingsApi
from langboard_shared.core.broker import Broker
from langboard_shared.core.caching import Cache
from langboard_shared.core.caching.InMemoryCache import InMemoryCache
from langboard_shared.core.db import DbSession
from langboard_shared.core.routing import AppRouter
from langboard_shared.core.security import AuthSecurity
from langboard_shared.core.storage import FileModel
from langboard_shared.domain.models import (
    CardAttachment,
    CardDocumentArtifact,
    CardMetadata,
    DocumentVectorCleanup,
    EmployeeMembershipPolicy,
    InternalBot,
    ProjectRole,
    SettingRole,
    User,
    UserIdentityLink,
)
from langboard_shared.domain.models.BaseBotModel import BotPlatform, BotPlatformRunningType
from langboard_shared.domain.models.DocumentEmbeddingCache import (
    DocumentEmbeddingCache,
    DocumentEmbeddingCacheReference,
)
from langboard_shared.domain.models.DocumentExtractionCache import (
    DocumentExtractionCache,
    DocumentExtractionCacheReference,
)
from langboard_shared.domain.models.InternalBot import InternalBotType
from langboard_shared.domain.services import DomainService
from langboard_shared.domain.services.factory.DoclingMetadataService import DoclingMetadataService
from langboard_shared.Env import Env
from langboard_shared.publishers import InternalBotPublisher
from langboard_shared.tasks.docling import DocumentEmbeddingTask
from langboard_shared.tasks.docling.DocumentSqliteStore import open_sqlite_vector_store
from langchain_core.embeddings import Embeddings
from sqlalchemy import update


@pytest.mark.skipif(
    os.environ.get("LANGBOARD_RUN_RETRIEVAL_BROWSER") != "1", reason="Explicit local browser bridge gate"
)
def test_production_settings_save_to_explicit_native_chunks(mirror_board, monkeypatch, tmp_path):
    b = mirror_board
    for model in (
        CardAttachment,
        CardDocumentArtifact,
        DocumentExtractionCache,
        DocumentExtractionCacheReference,
        CardMetadata,
        InternalBot,
        SettingRole,
        EmployeeMembershipPolicy,
        UserIdentityLink,
        DocumentVectorCleanup,
        DocumentEmbeddingCache,
        DocumentEmbeddingCacheReference,
    ):
        model.__table__.create(b.engine)
    monkeypatch.setattr(Cache, "_cache", InMemoryCache())
    monkeypatch.setattr(type(Env), "DATA_DIR", property(lambda _: tmp_path))
    monkeypatch.setattr(type(Env), "CARD_INTERNAL_ACCESS_MODE", property(lambda _: "project_members"))
    monkeypatch.setenv("MODEL_PROVIDER_ALLOWED_BASE_URLS", "https://fixture.invalid")
    text = "alpha 한국어 문서 日本語の資料 中文资料 beta " * 24
    original_file = tmp_path / "fixture.md"
    original_file.write_bytes(text.encode())
    original_hash = sha256(original_file.read_bytes()).hexdigest()
    config = dict(
        agent_llm="OpenAI Compatible",
        base_url="https://fixture.invalid",
        model_name="local-fixture",
        api_key="",
        retrieval=dict(enabled=False, store="sqlite", dimensions=3),
    )
    with DbSession.use(readonly=False) as db:
        db.exec(update(User).where(User.id == b.actor.id).values(is_admin=True))
        db.exec(update(ProjectRole).where(ProjectRole.user_id == b.actor.id).values(actions=["read", "card_update"]))
        db.insert(SettingRole(user_id=b.actor.id, actions=["internal_bot_update"]))
        binding = InternalBot(
            bot_type=InternalBotType.DocumentEmbedding,
            is_default=True,
            display_name="Local embedding",
            platform=BotPlatform.Default,
            platform_running_type=BotPlatformRunningType.Default,
            value=json.dumps(config),
        )
        db.insert(binding)
        attachment = CardAttachment(
            card_id=b.source.id,
            user_id=b.actor.id,
            filename="fixture.md",
            content_sha256=original_hash,
            file=FileModel(
                storage_type="local",
                storage_name="card_attachment",
                filename="fixture.md",
                original_filename="fixture.md",
                path="/fixture.md",
            ),
        )
        db.insert(attachment)
    with DbSession.use(readonly=False) as db:
        extraction = DocumentExtractionCache(cache_key=sha256(b"published-fixture-parser").hexdigest(), markdown=text)
        db.insert(extraction)
        db.insert(
            DocumentExtractionCacheReference(
                cache_id=extraction.id,
                use_token="a" * 32,
                source_type="card_attachment",
                source_uid=attachment.get_uid(),
                generation="b" * 32,
            )
        )
    service = DomainService()
    service.docling_metadata.upsert_document(
        CardMetadata,
        b.source,
        dict(
            attachment_uid=attachment.get_uid(),
            status="indexed",
            generation="b" * 32,
            content_hash=original_hash,
            content={"markdown": text},
        ),
    )
    # Socket publication and the delivery transport are irrelevant to the local UI/native bridge.
    monkeypatch.setattr(InternalBotPublisher, "updated", Mock())
    monkeypatch.setattr(DoclingMetadataService, "publish_update", Mock())
    queued, inference, processed = [], [], []

    def send_task(name, *, args, kwargs, **limits):
        if name.endswith("index_transcribed_attachment"):
            queued.append((args, kwargs))

    monkeypatch.setattr(Broker.celery, "send_task", send_task)

    class LocalEmbeddings(Embeddings):
        def embed_documents(self, texts):
            inference.append(list(texts))
            return [[float("alpha" in item), float("beta" in item), 0.1] for item in texts]

        def embed_query(self, text):
            raise AssertionError("Processing must not search")

    monkeypatch.setattr(DocumentEmbeddingTask, "create_document_embeddings", lambda *_: LocalEmbeddings())
    app = FastAPI()
    app.include_router(AppRouter.api)
    for route in app.routes:
        if getattr(route, "endpoint", None) in (
            InternalBotSettingsApi.update_internal_bot,
            BoardCardAttachmentApi.process_card_attachment_document,
        ):
            for dependency in route.dependant.dependencies:
                if dependency.name == "service":
                    app.dependency_overrides[dependency.call] = lambda: service
    app.add_middleware(RoleMiddleware, routes=AppRouter.api.routes)
    app.add_middleware(ApiAuthMiddleware, routes=AppRouter.api.routes)
    access, refresh = AuthSecurity.authenticate(b.actor.id)

    def document():
        return service.docling_metadata.get_document_by_attachment_uid(CardMetadata, b.source, attachment.get_uid())

    def state():
        current = service.internal_bot.get_current_by_id_like(binding.id)
        return dict(
            chunk_size=json.loads(current.value)["retrieval"].get("splitter", {}).get("chunk_size", 1000),
            queued=len(queued),
            inference_calls=len(inference),
            vector_files=len(list(tmp_path.rglob("*.sqlite"))),
        )

    with TestClient(app) as client:
        client.cookies.set(Env.REFRESH_TOKEN_NAME, refresh)
        path = f"/settings/internal-bot/{binding.get_uid()}"
        assert client.put(path, data={"value": json.dumps(config)}).status_code == 401
        # Bootstrap supplies only a local test JWT and public models; provider secret is empty.
        actor_data = service.user.get_by_id_like(b.actor.id).api_response()
        actor_data.update(is_admin=True, setting_role_actions=["internal_bot_update"], preferred_lang="en-US")
        bootstrap = dict(
            access=access,
            actor=actor_data,
            bot=binding.api_response(is_setting=True),
            card={**b.source.api_response(), "current_auth_role_actions": ["read", "card_update"]},
            attachment=attachment.api_response(),
            project_uid=b.source_board.get_uid(),
        )

        class Bridge(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def handle_request(self):
                try:
                    if self.path == "/__native/bootstrap":
                        status, content = 200, bootstrap
                    elif self.path == "/__native/state":
                        status, content = 200, state()
                    elif self.path == "/__native/work" and self.command == "POST":
                        before = document()
                        assert len(queued) == len(processed) + 1
                        frozen_size = before["embedding_config"]["retrieval"]["splitter"]["chunk_size"]
                        args, kwargs = queued[len(processed)]
                        assert kwargs == {} and len(args) == 1
                        payload = json.loads(args[0])
                        assert payload == dict(
                            attachment_uid=attachment.get_uid(),
                            generation=before["generation"],
                            request_uid=before["embedding"]["request_uid"],
                        )
                        asyncio.run(DocumentEmbeddingTask.embed_transcription(service, **payload))
                        after = document()
                        assert after["embedding"]["status"] == "indexed", after["embedding"]
                        pointer = after["embedding"]["pointer"]
                        with open_sqlite_vector_store(
                            tmp_path / "document-retrieval" / (pointer["embedding_fingerprint"] + ".sqlite"),
                            None,
                            dimensions=3,
                        ) as store:
                            chunks = store.get_by_ids(pointer["chunk_ids"])
                        content = dict(
                            status=after["embedding"]["status"],
                            frozen_chunk_size=frozen_size,
                            chunk_count=len(chunks),
                            maximum_chunk_length=max(len(c.page_content) for c in chunks),
                            source_unchanged=after["content"]["markdown"] == text
                            and after["content_hash"] == original_hash
                            and sha256(original_file.read_bytes()).hexdigest() == original_hash,
                            provenance_valid=all(
                                c.metadata["attachment_uid"] == attachment.get_uid()
                                and c.metadata["card_uid"] == b.source.get_uid()
                                and c.metadata["board_uid"] == b.source_board.get_uid()
                                and c.metadata["content_hash"] == original_hash
                                for c in chunks
                            ),
                        )
                        processed.append(content)
                        status = 200
                    else:
                        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                        headers = {
                            key: value
                            for key, value in self.headers.items()
                            if key.lower() not in ("host", "content-length")
                        }
                        response = client.request(self.command, self.path, content=body, headers=headers)
                        self.send_response(response.status_code)
                        self.send_header("Content-Type", "application/json")
                        self.end_headers()
                        self.wfile.write(response.content)
                        return
                    data = json.dumps(content, default=str).encode()
                    self.send_response(status)
                except Exception as error:
                    import traceback

                    traceback.print_exc()
                    data = json.dumps(dict(error=type(error).__name__, detail=str(error))).encode()
                    self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(data)

            do_GET = handle_request
            do_POST = handle_request
            do_PUT = handle_request

        server = ThreadingHTTPServer(("127.0.0.1", 0), Bridge)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        root = Path(__file__).resolve().parents[4]
        artifact = root / ".verification/card-work-wave-20261025/retrieval-settings"
        artifact.mkdir(parents=True, exist_ok=True)
        try:
            result = subprocess.run(
                ["node_modules/.bin/playwright", "test", "-c", "playwright.document-retrieval-native.config.ts"],
                cwd=root / "src/ui",
                env={**os.environ, "LANGBOARD_RETRIEVAL_BRIDGE_URL": f"http://127.0.0.1:{server.server_port}"},
                text=True,
                capture_output=True,
                timeout=150,
            )
            (artifact / "browser.log").write_text(result.stdout + result.stderr)
            assert result.returncode == 0, result.stdout + result.stderr
            assert len(processed) == 2 and processed[1]["chunk_count"] > processed[0]["chunk_count"]
            assert document()["content_hash"] == original_hash
            (artifact / "native-result.json").write_text(
                json.dumps(dict(processed=processed, final=state(), source_hash=original_hash), indent=2)
            )
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
            service.close()
