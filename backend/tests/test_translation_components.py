"""术语表 / PromptBuilder / Sanitizer / Cache 测试（规格第 32 节）。"""

from translation.cache import TranslationCache, normalize
from translation.glossary import extract_relevant_terms, load_glossary
from translation.prompt_builder import PromptBuilder
from translation.sanitizer import TranslationSanitizer

GLOSSARY = {
    "ジェット": "捷风",
    "ロー": "残血",
    "裏": "绕后",
    "ミッド": "中路",
    "サイト": "包点",
    "オペ": "Operator",
    "ヘブン": "Heaven",
}


def test_load_glossary_from_resource() -> None:
    glossary = load_glossary()
    assert glossary.get("ジェット") == "捷风"
    assert glossary.get("ロー") == "残血"


def test_extract_relevant_terms_only_present() -> None:
    hits = extract_relevant_terms("ジェットロー、裏来てる", GLOSSARY)
    # 只返回出现的术语，且值为中文
    assert "ジェット" in hits
    assert "ロー" in hits
    assert "裏" in hits
    assert "ミッド" not in hits  # 未出现
    assert hits["ジェット"] == "捷风"


def test_extract_long_term_precedence() -> None:
    # "ミッド" 应被命中；更长的词优先只是排序，不影响结果正确性
    hits = extract_relevant_terms("ミッド二人", GLOSSARY)
    assert "ミッド" in hits


def test_prompt_builder_no_terms() -> None:
    pb = PromptBuilder({})
    msgs = pb.build_messages("こんにちは")
    assert msgs[0]["role"] == "system"
    user = msgs[1]["content"]
    assert "こんにちは" in user
    assert "参考下面的翻译" not in user  # 无术语则不插入术语块


def test_prompt_builder_with_terms() -> None:
    pb = PromptBuilder(GLOSSARY)
    msgs = pb.build_messages("ジェットロー")
    user = msgs[1]["content"]
    assert "ジェット 翻译成 捷风" in user
    assert "ロー 翻译成 残血" in user
    assert "ジェットロー" in user  # 原句保留


def test_sanitizer_strips_prefix_and_fence() -> None:
    s = TranslationSanitizer()
    assert s.clean("翻译：捷风残血") == "捷风残血"
    assert s.clean("```\n捷风残血\n```") == "捷风残血"
    assert s.clean("  捷风残血  ") == "捷风残血"
    assert s.clean("") == ""


def test_sanitizer_max_chars() -> None:
    s = TranslationSanitizer(max_chars=5)
    assert s.clean("一二三四五六七八") == "一二三四五"


def test_cache_basic_and_eviction() -> None:
    cache = TranslationCache(max_size=2)
    cache.put("ジェット", "捷风")
    cache.put("ミッド", "中路")
    assert cache.get("ジェット") == "捷风"
    assert cache.get("不存在") is None
    # 命中「ジェット」使其变为最近使用；插入第三个时淘汰「ミッド」
    cache.put("ロー", "残血")
    assert cache.get("ジェット") == "捷风"  # 最近使用，仍保留
    assert cache.get("ミッド") is None      # 最早未使用，被淘汰
    assert cache.get("ロー") == "残血"
    assert len(cache) == 2


def test_cache_lru_move_to_end() -> None:
    cache = TranslationCache(max_size=2)
    cache.put("a", "A")
    cache.put("b", "B")
    cache.get("a")  # a 变为最近使用
    cache.put("c", "C")  # 淘汰 b
    assert cache.get("a") == "A"
    assert cache.get("b") is None
    assert cache.get("c") == "C"


def test_normalize_key() -> None:
    assert normalize("  x  ") == "x"
    assert normalize("") == ""
