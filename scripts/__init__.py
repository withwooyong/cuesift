"""리포지토리 보조 스크립트.

**`fetch_ted2020.py`는 표준 라이브러리만 쓴다(스펙 §3.4)** — 제품 없이 도는
획득 스크립트라 의존성을 늘리지 않는다는 제약이 붙는다. `glossary_verify.py`는
`cuesift`·`bench`를 임포트하므로 이 제약의 대상이 아니다.
"""

from __future__ import annotations
