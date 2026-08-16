import pytest

import extract


def test_normalize_zero_becomes_one():
    recipes = [{"ingredients": [
        {"quantity": 0, "food": "salami"},
        {"quantity": 2, "food": "eggs"},
        {"quantity": None, "food": "salt"},
        {"quantity": 1.5, "food": "flour"},
    ]}]
    out = extract._normalize(recipes)[0]["ingredients"]
    assert out[0]["quantity"] == 1      # 0 -> 1
    assert out[1]["quantity"] == 2      # untouched
    assert out[2]["quantity"] is None   # genuine null kept
    assert out[3]["quantity"] == 1.5    # untouched


def test_normalize_notes_coercion():
    recipes = [{"notes": [
        {"title": "Storage", "text": "Keeps 3 days."},   # kept
        {"title": "Bare title", "text": ""},             # dropped: no text
        {"title": "", "text": "  Freeze flat.  "},       # kept, text stripped, blank title ok
        {"title": None, "text": None},                   # dropped: no text
        {"text": 5},                                     # coerced to "5", kept
    ]}]
    out = extract._normalize(recipes)[0]["notes"]
    assert out == [
        {"title": "Storage", "text": "Keeps 3 days."},
        {"title": "", "text": "Freeze flat."},
        {"title": "", "text": "5"},
    ]


def test_normalize_notes_defaults_and_shapes():
    # missing key -> [], a single dict gets wrapped, a non-list -> []
    assert extract._normalize([{}])[0]["notes"] == []
    assert extract._normalize([{"notes": {"text": "x"}}])[0]["notes"] == [{"title": "", "text": "x"}]
    assert extract._normalize([{"notes": "nope"}])[0]["notes"] == []


def test_parse_recipes_plain_json():
    raw = '{"recipes": [{"name": "X"}]}'
    assert extract.parse_recipes(raw) == [{"name": "X"}]


def test_parse_recipes_strips_code_fence():
    raw = '```json\n{"recipes": [{"name": "Y"}]}\n```'
    assert extract.parse_recipes(raw) == [{"name": "Y"}]


def test_parse_recipes_extracts_from_prose():
    raw = 'Sure! Here you go:\n{"recipes": [{"name": "Z"}]}\nHope that helps.'
    assert extract.parse_recipes(raw) == [{"name": "Z"}]


# ── unified multi-source extraction ──────────────────────────────────────

def test_from_sources_combines_caption_transcript_and_image(monkeypatch):
    captured = {}

    def fake_structure(content):
        captured["content"] = content
        return [{"name": "Reel dish"}]

    monkeypatch.setattr(extract, "_structure", fake_structure)
    monkeypatch.setattr(extract, "is_video_url", lambda u: True)
    monkeypatch.setattr(extract, "_video_metadata",
                        lambda u: {"title": "T", "description": "200g flour", "thumbnail": "http://x/t.jpg"})
    monkeypatch.setattr(extract, "image_to_data_url", lambda p: "data:image/jpeg;base64,AAAA")
    import transcribe
    monkeypatch.setattr(transcribe, "transcribe_audio", lambda p, **k: "mix and bake for 20 min")

    out = extract.extract_recipes_from_sources(
        image_paths=["/tmp/x.jpg"], url="http://insta/reel", audio_path="/tmp/n.webm",
    )

    assert out[0]["name"] == "Reel dish"
    assert out[0]["image_url"] == "http://x/t.jpg"          # link thumbnail → dish photo
    assert out[0]["source_url"] == "http://insta/reel"
    text = captured["content"][0]["text"]
    assert "LINKED POST CAPTION" in text and "200g flour" in text       # caption (ingredients)
    assert "SPOKEN (transcribed" in text and "mix and bake" in text     # voice-over (steps)
    assert any(part.get("type") == "image_url" for part in captured["content"])  # image carried


def test_from_sources_requires_at_least_one_source():
    with pytest.raises(ValueError):
        extract.extract_recipes_from_sources()


def test_build_user_prompt_includes_ai_rules():
    prompt = extract.build_user_prompt(
        target_language="English",
        user_note="no onions",
        ai_rules="Always substitute butter with olive oil. Default to Polish."
    )
    assert "Household & dietary rules (ALWAYS follow these): Always substitute butter with olive oil. Default to Polish." in prompt
    assert "Extra instructions from the user: no onions" in prompt
