"""Validate native vector source identity without changing legacy card shapes."""


def document_source_identity(source: dict, *, require_generation: bool = False) -> dict:
    kind = source.get("source_type", "card_attachment")
    if kind == "comment_compaction":
        fields = ["board_uid", "card_uid", "compaction_uid", "source_revision", "content_hash", "embedding_fingerprint"]
        if require_generation:
            fields.append("generation")
        if "attachment_uid" in source or "wiki_uid" in source or not all(
            isinstance(source.get(key), str) and source[key] for key in fields
        ):
            raise ValueError("Incomplete compaction vector source")
        return {"source_type": kind, **{key: source[key] for key in fields}}
    if kind not in ("card_attachment", "project_attachment", "wiki_attachment"):
        raise ValueError("Unknown vector source type")
    forbidden = (
        ("card_uid", "wiki_uid")
        if kind == "project_attachment"
        else (("card_uid",) if kind == "wiki_attachment" else ("wiki_uid",))
    )
    if any(key in source for key in forbidden):
        raise ValueError("Conflicting vector source owner")
    fields = ["board_uid", "attachment_uid", "content_hash", "embedding_fingerprint"]
    if kind == "card_attachment":
        fields.insert(1, "card_uid")
    if kind == "wiki_attachment":
        fields.insert(1, "wiki_uid")
    if require_generation:
        fields.append("generation")
    if not all(isinstance(source.get(key), str) and source[key] for key in fields):
        raise ValueError("Incomplete vector source")
    identity = {key: source[key] for key in fields}
    if "source_type" in source:
        identity["source_type"] = kind
    return identity
