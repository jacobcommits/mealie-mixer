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


def test_parse_recipes_plain_json():
    raw = '{"recipes": [{"name": "X"}]}'
    assert extract.parse_recipes(raw) == [{"name": "X"}]


def test_parse_recipes_strips_code_fence():
    raw = '```json\n{"recipes": [{"name": "Y"}]}\n```'
    assert extract.parse_recipes(raw) == [{"name": "Y"}]


def test_parse_recipes_extracts_from_prose():
    raw = 'Sure! Here you go:\n{"recipes": [{"name": "Z"}]}\nHope that helps.'
    assert extract.parse_recipes(raw) == [{"name": "Z"}]
