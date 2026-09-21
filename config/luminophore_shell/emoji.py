from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EmojiEntry:
    glyph: str
    name: str
    keywords: tuple[str, ...]


EMOJI = (
    EmojiEntry("😀", "웃는 얼굴", ("smile", "happy", "기쁨")),
    EmojiEntry("😂", "기쁨의 눈물", ("laugh", "lol", "웃음")),
    EmojiEntry("🥰", "사랑스러운 얼굴", ("love", "heart", "사랑")),
    EmojiEntry("😎", "선글라스 얼굴", ("cool", "멋짐")),
    EmojiEntry("🤔", "생각하는 얼굴", ("think", "고민")),
    EmojiEntry("😭", "크게 우는 얼굴", ("cry", "슬픔")),
    EmojiEntry("😡", "화난 얼굴", ("angry", "분노")),
    EmojiEntry("👍", "좋아요", ("thumb", "yes", "동의")),
    EmojiEntry("👎", "싫어요", ("thumb", "no", "반대")),
    EmojiEntry("👏", "박수", ("clap", "축하")),
    EmojiEntry("🙏", "감사와 부탁", ("thanks", "please", "감사")),
    EmojiEntry("💪", "힘", ("strong", "muscle")),
    EmojiEntry("❤️", "빨간 하트", ("heart", "love", "사랑")),
    EmojiEntry("💙", "파란 하트", ("heart", "blue")),
    EmojiEntry("✨", "반짝임", ("sparkle", "shine")),
    EmojiEntry("🔥", "불", ("fire", "hot")),
    EmojiEntry("🎉", "파티", ("party", "celebrate", "축하")),
    EmojiEntry("✅", "완료", ("check", "done", "확인")),
    EmojiEntry("❌", "실패", ("cross", "fail", "취소")),
    EmojiEntry("⚠️", "경고", ("warning", "주의")),
    EmojiEntry("💡", "아이디어", ("idea", "light")),
    EmojiEntry("🚀", "로켓", ("rocket", "launch")),
    EmojiEntry("💻", "노트북", ("computer", "code", "개발")),
    EmojiEntry("🎮", "게임", ("game", "controller")),
    EmojiEntry("🎵", "음악", ("music", "note")),
    EmojiEntry("📌", "핀", ("pin", "mark")),
    EmojiEntry("📅", "달력", ("calendar", "date")),
    EmojiEntry("🔒", "잠금", ("lock", "secure", "보안")),
    EmojiEntry("🌙", "달", ("moon", "night")),
    EmojiEntry("☀️", "해", ("sun", "day")),
)


def search_emoji(query: str, limit: int = 30) -> tuple[EmojiEntry, ...]:
    needle = query.strip().casefold()
    if not needle:
        return EMOJI[:limit]
    return tuple(
        item for item in EMOJI
        if needle in " ".join((item.name, *item.keywords)).casefold()
    )[:limit]
