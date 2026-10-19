"""Reuse one bounded recovery invocation for derived document work."""

from .DocumentCleanupTask import recover_cleanup
from .DocumentKeywordTask import recover_keyword


if __name__ == "__main__":
    recover_cleanup()
    recover_keyword()
