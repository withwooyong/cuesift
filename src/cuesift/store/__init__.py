"""캐시와 재개 상태 (요구사항정의서 §7.1 `store/` · NFR-3 · FR-2.7).

**재개는 별도 상태 파일이 아니라 캐시다** (설계 §4.2). `iter_batches`가
연속 구간을 전제하므로 재개해도 전체 트랙을 넘겨야 하고, 그러면 호출을
줄이는 유일한 수단이 캐시다.
"""

from __future__ import annotations

from cuesift.store.cache import CacheRequest, load, store
from cuesift.store.provider import CachingProvider

__all__ = ["CacheRequest", "CachingProvider", "load", "store"]
