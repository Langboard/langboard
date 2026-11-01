"""Card-scoped native image content; no arbitrary URL fetching or credentials."""

import base64
import json
from html.parser import HTMLParser
from typing import Annotated
from urllib.parse import unquote, urljoin, urlsplit
from fastmcp.tools import ToolResult
from langboard_shared.core.security.CollaborationChannel import CollaborationChannel
from langboard_shared.core.storage import Storage
from langboard_shared.core.storage.BaseStorage import BaseStorage
from langboard_shared.domain.models import Bot, ProjectRole, User
from langboard_shared.domain.models.ProjectRole import ProjectRoleAction
from langboard_shared.domain.services import DomainService
from langboard_shared.domain.services.CardCommentImageAnchors import (
    MAX_IMAGE_BYTES,
    image_metadata,
)
from langboard_shared.domain.services.CardCommentImageAnchors import (
    BoundedImageBuffer as _BoundedBuffer,
)
from langboard_shared.Env import Env
from langboard_shared.security import RoleFinder
from markdown_it import MarkdownIt
from mcp.types import ImageContent, TextContent
from pydantic import Field, StrictBool
from ..mcp_integration.RoleFilter import McpRoleFilter
from ..mcp_integration.Tool import McpTool


MAX_IMAGES = 10
MAX_TOTAL_BYTES = MAX_IMAGE_BYTES


class _ImageHTML(HTMLParser):
    def __init__(self):
        super().__init__()
        self.urls = []

    def handle_starttag(self, tag, attrs):
        if tag == "img" and dict(attrs).get("src"):
            self.urls.append(dict(attrs)["src"])


def _image_urls(value):
    if isinstance(value, str):
        if value.lstrip().startswith(("{", "[")):
            try:
                decoded = json.loads(value)
            except ValueError:
                pass
            else:
                return _image_urls(decoded)
        urls = []

        def visit(tokens):
            for token in tokens:
                if token.type == "image" and token.attrGet("src"):
                    urls.append(token.attrGet("src"))
                elif token.type in {"html_inline", "html_block"}:
                    parser = _ImageHTML()
                    parser.feed(token.content)
                    urls.extend(parser.urls)
                if token.children:
                    visit(token.children)

        visit(MarkdownIt("commonmark").parse(value))
        return urls
    if isinstance(value, list):
        return [url for item in value for url in _image_urls(item)]
    if isinstance(value, dict):
        if value.get("type") in {"img", "image"}:
            source = value.get("url") or value.get("src")
            return [source] if isinstance(source, str) else []
        return [url for key in ("content", "children", "text") for url in _image_urls(value.get(key))]
    return []


def _stored_image_path(url):
    parsed = urlsplit(urljoin(Env.PUBLIC_UI_URL + "/", url))
    origins = {(urlsplit(base).scheme, urlsplit(base).netloc) for base in (Env.PUBLIC_UI_URL, Env.API_URL)}
    if (
        (parsed.scheme, parsed.netloc) not in origins
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        return None
    path = unquote(parsed.path)
    prefix = urlsplit(Env.API_URL).path.rstrip("/")
    if prefix and path.startswith(prefix + "/"):
        path = path[len(prefix) :]
    parts = path.split("/")
    if len(parts) != 5 or parts[:2] != ["", "file"] or parts[3] != "card_attachment":
        return None
    if not parts[2] or parts[4] in {"", ".", ".."} or "\\" in path:
        return None
    try:
        storage_type = BaseStorage.decrypt_storage_type(parts[2])
    except (ValueError, UnicodeError):
        return None
    if not storage_type:
        return None
    return storage_type, parts[3], parts[4]


@McpTool.add(
    description="Read native image content from card body storage references and selected same-card attachments. Reports omissions; no external URL fetch or document extraction. Use include_body_images=false for attachment-only reads."
)
@McpRoleFilter.add(ProjectRole, [ProjectRoleAction.Read], RoleFinder.project)
def read_card_images(
    project_uid: str,
    card_uid: str,
    user_or_bot: User | Bot,
    service: DomainService,
    include_attachment_uids: Annotated[list[str], Field(max_length=25)] | None = None,
    include_body_images: StrictBool = True,
) -> ToolResult:
    params = service.card.resolve_readable_card(project_uid, card_uid, user_or_bot, CollaborationChannel.Mcp)
    if not params:
        raise ValueError("Card not found in project")
    _, card, _ = params
    candidates = []
    if include_body_images:
        blocks = service.card_content_block.get_blocks_by_card(card)
        if blocks:
            urls = [
                url
                for block in blocks
                if block.block_type == "rich_text"
                for url in _image_urls(block.payload.get("text", ""))
            ]
        else:
            urls = _image_urls(card.description.content)
        candidates = [("body", url, None) for url in urls]
    omitted = []
    for uid in dict.fromkeys(include_attachment_uids or []):
        row = service.card_attachment.get_by_id_like(uid, consistent=True)
        if row is None or row.card_id != card.id or row.replaced_by_id is not None:
            omitted.append({"attachment_uid": uid, "reason": "attachment_not_in_card"})
        else:
            candidates.append(("attachment", row.file.path, uid))
    images, included, seen = [], [], set()
    total = 0
    for source, url, uid in candidates:
        stored = _stored_image_path(url)
        reason = None
        if stored is None:
            reason = "unsupported_source"
        else:
            owner = service.card_attachment.resolve_file_owner(*stored)
            if (
                owner is None
                or owner.card_id != card.id
                or owner.replaced_by_id is not None
                or (owner.file.storage_type, owner.file.storage_name, owner.file.filename) != stored
                or (uid is not None and owner.get_uid() != uid)
            ):
                reason = "attachment_not_in_card"
        if reason is not None:
            omitted.append({"source": source, "attachment_uid": uid, "reason": reason})
            continue
        uid, revision = owner.get_uid(), owner.image_revision
        if not service.card.resolve_readable_card(project_uid, card_uid, user_or_bot, CollaborationChannel.Mcp):
            omitted.append({"source": source, "attachment_uid": uid, "reason": "source_changed"})
            continue
        if stored in seen:
            continue
        elif len(images) >= MAX_IMAGES:
            reason = "image_count_limit"
        else:
            seen.add(stored)
            with _BoundedBuffer(MAX_TOTAL_BYTES - total) as buffer:
                try:
                    found = Storage.download_file(owner.file, buffer)
                except OSError:
                    found = False
                payload = buffer.getvalue()
                try:
                    mime, image_width, image_height = image_metadata(payload)
                    media_error = None
                except ValueError as exc:
                    media_error = str(exc)
                if buffer.exceeded:
                    reason = "byte_limit"
                elif (
                    not service.card.resolve_readable_card(project_uid, card_uid, user_or_bot, CollaborationChannel.Mcp)
                    or (current := service.card_attachment.resolve_file_owner(*stored)) is None
                    or current.card_id != card.id
                    or current.get_uid() != uid
                    or current.replaced_by_id is not None
                    or current.image_revision != revision
                    or (current.file.storage_type, current.file.storage_name, current.file.filename) != stored
                ):
                    reason = "source_changed"
                elif not found:
                    reason = "content_unavailable"
                elif media_error is not None:
                    reason = media_error
                else:
                    total += len(payload)
                    images.append(
                        ImageContent(type="image", data=base64.b64encode(payload).decode("ascii"), mime_type=mime)
                    )
                    included.append(
                        {
                            "source": source,
                            "attachment_uid": uid,
                            "image_revision": revision,
                            "bytes": len(payload),
                            "mime_type": mime,
                            "image_width": image_width,
                            "image_height": image_height,
                        }
                    )
        if reason:
            omitted.append({"source": source, "attachment_uid": uid, "reason": reason})
    manifest = {"included": included, "omitted": omitted, "total_bytes": total}
    return ToolResult(
        content=[TextContent(type="text", text=json.dumps(manifest)), *images], structured_content=manifest
    )
