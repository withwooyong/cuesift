"""프롬프트 조립 (FR-2.2 맥락 윈도우, FR-2.3 용어집, FR-2.8 작품 맥락).

**맥락으로 원문만 준다.** 앞의 번역 결과를 주면 용어 일관성을 프롬프트가
담당하게 되는데, 그것은 용어집(FR-2.3)이 존재하는 이유 그 자체다. 같은
문제를 토큰을 더 써서 비결정적으로 다시 푸는 것이고, 그 대가로 재현성
(NFR-3)과 병렬성과 캐시 키(WP7b)가 동시에 깨진다 (설계 §5.2).

이 모듈은 순수하다. 네트워크도 파일도 건드리지 않으므로 테스트가 값싸다.
"""

from __future__ import annotations

from collections.abc import Sequence

from cuesift.glossary import Glossary, GlossaryEntry
from cuesift.segment.models import Segment
from cuesift.translate.provider import ChatMessage

# 중괄호가 두 겹인 것은 `.format()` 때문이다. 한 겹으로 줄이면 format이 그
# 자리를 치환 필드로 읽어 `KeyError: '"translations"'`가 나고, 그것도
# 모듈 임포트가 아니라 build_messages 호출마다 터진다.
_SYSTEM_BASE = """\
너는 자막 번역가다. {source_lang} 자막을 {target_lang}로 번역한다.

규칙:
- 각 세그먼트를 독립적으로 번역한다. 세그먼트를 합치거나 나누지 마라.
- 번역 대상으로 주어진 항목만 번역한다.
- 응답은 다른 말 없이 JSON 하나로만 낸다:
  {{"translations": [{{"id": <번호>, "text": "<번역문>"}}]}}
- 주어진 번호를 그대로 쓰고, 빠뜨리거나 더하지 마라."""


def _format_lines(segments: Sequence[Segment]) -> str:
    return "\n".join(f"[{s.index}] {s.source_text}" for s in segments)


def _reject_index_collisions(segments: Sequence[Segment]) -> None:
    """맥락과 번역 대상을 통틀어 번호가 겹치지 않는지 본다.

    겹치면 같은 번호가 "번역하지 말 것"과 "번역 대상"에 동시에 실려, 전역
    인덱스를 쓰는 이유(모델이 둘을 구별할 수 있게) 자체가 무너진다. 모델이
    그 번호를 맥락으로 읽고 빠뜨리면 개수 불일치로 폴백이 상시 발동하는데,
    그때 드러나는 것은 "모델이 말을 안 듣는다"이지 맥락 윈도우 계산의
    off-by-one이 아니다. 배치 안의 번호 중복도 같은 자리에서 걸린다 - 이
    프롬프트가 응답에 요구하는 키가 번호(`{"id": <번호>}`)뿐이라, 번호가
    겹치면 돌아온 번역이 어느 세그먼트의 것인지 가릴 수 없다.
    """
    seen: set[int] = set()
    for segment in segments:
        if segment.index in seen:
            raise ValueError(f"세그먼트 번호({segment.index})가 맥락과 번역 대상에 중복해 나온다")
        seen.add(segment.index)


def _collect_terms(glossary: Glossary, segments: Sequence[Segment]) -> list[GlossaryEntry]:
    """등장하는 용어를 **용어집 등재 순**으로 모은다 (FR-2.3).

    세그먼트를 돌며 나온 순서대로 붙이면 안 된다. `terms_in`이 원문 하나
    안에서 등재 순을 지켜도, 세그먼트 사이의 순서는 문서 등장 순이 되어
    같은 용어 집합이 배치 내용에 따라 다르게 직렬화된다. 그러면 프롬프트
    프리픽스 캐시가 무효화되고 "같은 입력에 같은 결과"(NFR-3)가 깨진다.

    그래서 매치된 것을 모으는 대신 **`glossary.entries`를 거른다.** set은
    멤버십 판정에만 쓰고 출력 순서는 tuple인 `entries`가 정하므로, 해시
    순서가 결과에 새어 들어오지 않는다.

    source가 같은 항목이 둘 있으면 둘 다 남긴다. 하나로 줄이면 그 항목의
    대응어를 주입하지 않은 채 `violations()`가 위반으로 잡는다 - 용어집
    모듈이 "주입한 용어와 위반으로 잡는 용어가 어긋나면 안 된다"고 못 박은
    바로 그 어긋남이다.
    """
    matched = {
        entry.source for segment in segments for entry in glossary.terms_in(segment.source_text)
    }
    return [entry for entry in glossary.entries if entry.source in matched]


def build_messages(
    batch: Sequence[Segment],
    *,
    source_lang: str,
    target_lang: str,
    before: Sequence[Segment] = (),
    after: Sequence[Segment] = (),
    glossary: Glossary | None = None,
    work_context: str | None = None,
) -> list[ChatMessage]:
    """배치 하나를 번역시킬 메시지를 만든다.

    식별자는 `Segment.index`(원본 전역 인덱스)다. 배치 내 지역 번호를 쓰면
    맥락 세그먼트와 번호 공간이 갈라져, 모델이 "[2]"가 맥락인지 번역
    대상인지 구별할 근거를 잃는다.
    """
    if not batch:
        # 빈 배치를 그대로 조립하면 "번역할 것이 없는" 프롬프트가 완성되고,
        # 모델은 빈 목록을 돌려주고, 개수 검증(기대 0 = 실제 0)마저 통과한다.
        # 배치 분할의 버그가 성공한 호출로 위장돼 요금만 나가고 끝난다.
        raise ValueError("번역 대상 배치가 비어 있다")

    context_and_batch = [*before, *batch, *after]
    _reject_index_collisions(context_and_batch)

    system_parts = [_SYSTEM_BASE.format(source_lang=source_lang, target_lang=target_lang)]

    if work_context:
        # 지정되지 않았을 때 빈 절을 넣으면 모델이 빈 지시를 해석하려 든다.
        system_parts.append(f"작품 맥락:\n{work_context}")

    if glossary is not None:
        # 맥락 세그먼트의 용어도 포함한다. 앞 맥락에 나온 용어를 번역 대상이
        # 대명사로 받는 경우가 있어, 대상만 훑으면 그 용어가 주입되지 않는다.
        entries = _collect_terms(glossary, context_and_batch)
        if entries:
            lines = "\n".join(f"- {e.source} -> {' / '.join(e.targets)}" for e in entries)
            system_parts.append(f"용어집 (반드시 이 대응어를 쓴다):\n{lines}")

    user_parts: list[str] = []
    if before:
        user_parts.append(f"## 앞 맥락 - 번역하지 말 것\n{_format_lines(before)}")
    user_parts.append(f"## 번역 대상\n{_format_lines(batch)}")
    if after:
        user_parts.append(f"## 뒤 맥락 - 번역하지 말 것\n{_format_lines(after)}")

    return [
        ChatMessage(role="system", content="\n\n".join(system_parts)),
        ChatMessage(role="user", content="\n\n".join(user_parts)),
    ]
