import push


def test_fmt_quantity():
    assert push._fmt_quantity(1.0) == "1"      # whole float -> int-looking
    assert push._fmt_quantity(1.4) == "1.4"
    assert push._fmt_quantity(2) == "2"


def test_ingredient_to_text():
    assert push.ingredient_to_text(
        {"quantity": 1.0, "unit": "kg", "food": "ground beef", "note": "80/20"}
    ) == "1 kg ground beef, 80/20"
    assert push.ingredient_to_text(
        {"quantity": None, "unit": None, "food": "salt", "note": "to taste"}
    ) == "salt, to taste"
    assert push.ingredient_to_text(
        {"quantity": 2, "unit": None, "food": "eggs", "note": None}
    ) == "2 eggs"
