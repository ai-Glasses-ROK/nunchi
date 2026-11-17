"""프롬프트(LLM에게 주는 지시문) 만들기.

음성 비서용 프롬프트의 핵심
- 첫 문장에 결론 (귀로 듣는 사람은 앞부분만 기억합니다)
- 짧게 (TTS 시간 = 기다리는 시간)
- 마크다운·이모지 금지 (TTS가 '별표 별표'라고 읽거나 멈춥니다)
- 문서에 없는 말은 지어내지 않기 (RAG의 존재 이유)
"""

from __future__ import annotations

from nunchi.rag.retriever import Hit

VOICE_SYSTEM = """너는 사용자가 쓰고 있는 스마트 안경 속 음성 비서야. 너의 답은 안경 스피커로 소리 내어 읽힌다.
규칙:
1. 한국어로 1~3문장, 120자 안쪽으로 짧게 말한다. 질문에 대한 답을 첫 문장에 바로 말한다.
2. 마크다운, 목록 기호, 표, 이모지, 번호 인용을 쓰지 않는다. 사람에게 말하듯 자연스럽게 쓴다.
3. [참고 문서]가 주어지면 그 내용을 우선해서 답하고, 문서에 없는 사실을 지어내지 않는다.
4. {fallback}
5. 숫자와 단위는 읽기 쉽게 쓴다. 예: 2시간, 3층, 오후 9시.
6. 질문 자체가 부상·화재·중독 같은 응급 상황일 때만 안전 행동과 119 신고를 먼저 말한다. 그 밖에는 묻지 않은 주의사항을 덧붙이지 않는다."""

TEXT_SYSTEM = """너는 오프라인에서 동작하는 개인 지식 비서야.
규칙:
1. 한국어로 핵심부터 답한다. 필요하면 짧은 목록을 써도 된다.
2. [참고 문서]의 내용을 근거로 답하고, 근거로 쓴 문서 번호를 [1]처럼 문장 끝에 붙인다.
3. 문서에 없는 사실을 지어내지 않는다.
4. {fallback}"""

FALLBACK_SOFT = '문서에 답이 없으면 "문서에는 없지만"이라고 먼저 말한 뒤 일반 상식으로 짧게 답한다. 확실하지 않으면 모른다고 말한다.'
FALLBACK_STRICT = '문서에 답이 없으면 "문서에서 찾지 못했어요."라고만 답한다.'

ASR_HINT = (
    "질문은 음성 인식으로 받아 적은 글이라 발음이 비슷한 단어가 잘못 적혔을 수 있다(예: 초크→쇼크, 입실→입술). "
    "참고 문서에 발음이 비슷한 단어가 있으면 그 단어를 뜻한 것으로 이해하고 답한다."
)

CAPTION_PROMPT = (
    "사진에 보이는 글자(숫자·코드·제품명 포함)와 주요 사물을 검색어로 쓸 수 있게 한 줄로 적어라. "
    "문장으로 설명하지 말고 쉼표로 구분한 명사만 쓴다. 예: HG-2000 발전기 계기판, E2 코드, 빨간 경고등"
)

VISION_HINT = "첨부한 사진은 사용자가 지금 안경 카메라로 보고 있는 장면이다. 사진에서 보이는 것을 근거로 답한다."


def system_prompt(style: str = "voice", strict: bool = False, vision: bool = False, from_voice: bool = False) -> str:
    base = VOICE_SYSTEM if style == "voice" else TEXT_SYSTEM
    text = base.format(fallback=FALLBACK_STRICT if strict else FALLBACK_SOFT)
    if from_voice:
        text += "\n" + ASR_HINT
    if vision:
        text += "\n" + VISION_HINT
    return text


def format_context(hits: list[Hit], max_chars: int = 3500) -> str:
    """검색 결과를 번호를 붙여 하나의 문자열로. 너무 길면 뒤쪽 조각을 잘라냅니다."""
    parts: list[str] = []
    used = 0
    for n, h in enumerate(hits, start=1):
        body = h.chunk.text.strip()
        block = f"[{n}] {h.chunk.label}\n{body}"
        if used + len(block) > max_chars and parts:
            break
        parts.append(block)
        used += len(block)
    return "\n\n".join(parts)


def build_messages(
    question: str,
    hits: list[Hit],
    history: list[tuple[str, str]] | None = None,
    style: str = "voice",
    strict: bool = False,
    vision: bool = False,
    from_voice: bool = False,
) -> list[dict]:
    """채팅 API에 넣을 messages 배열.

    작은 모델일수록 '문서 + 질문'을 마지막 user 메시지 하나에 같이 넣는 편이 잘 따릅니다.
    이전 대화(history)는 문서 없이 질문/답만 넣어 토큰을 아낍니다.
    """
    messages: list[dict] = [{"role": "system", "content": system_prompt(style, strict, vision, from_voice)}]
    for user, assistant in history or []:
        messages.append({"role": "user", "content": user})
        messages.append({"role": "assistant", "content": assistant})
    if hits:
        content = f"[참고 문서]\n{format_context(hits)}\n\n[질문]\n{question}"
    else:
        content = f"[참고 문서]\n(관련 문서를 찾지 못함)\n\n[질문]\n{question}"
    messages.append({"role": "user", "content": content})
    return messages
