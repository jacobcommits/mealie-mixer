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
