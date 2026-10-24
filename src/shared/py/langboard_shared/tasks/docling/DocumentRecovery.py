"""Reuse one bounded recovery invocation for derived document work."""

from .CompactionMetadataIndexTask import recover_indexes
from .DocumentCleanupTask import recover_cleanup
from .DocumentKeywordTask import recover_keyword


def recover_card_location_embeddings(*, limit=100):
    from ...domain.services import DomainService

    service = DomainService()
    try:
        return service.docling_metadata.recover_card_location_embeddings(limit=limit)
    finally:
        service.close()


if __name__ == "__main__":
    recover_indexes()
    recover_cleanup()
    recover_keyword()
    recover_card_location_embeddings()
