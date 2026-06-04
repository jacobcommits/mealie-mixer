import cookbook


def test_is_recipe_text():
    assert cookbook._is_recipe_text("INGREDIENTS\n1 egg\nDIRECTIONS\nMix.")
    assert cookbook._is_recipe_text("ingredient list ... directions ...")
    # non-recipe pages are dropped
    assert not cookbook._is_recipe_text("Table of Contents\nRecipes Inspired by Africa")
    assert not cookbook._is_recipe_text("Ingredients only, no method")   # missing 'direction'
    assert not cookbook._is_recipe_text("")


def test_guess_title_skips_chrome():
    text = "\n".join([
        "10",                                   # page number
        "Table of Contents",                    # repeated footer link
        "Kofta (Turkey Kebabs)",                # the actual title
        "Prep: 15 minutes | Cook: 20 minutes",  # metadata line
        "Ingredients",
    ])
    assert cookbook._guess_title(text) == "Kofta (Turkey Kebabs)"


def test_guess_title_fallback():
    assert cookbook._guess_title("Ingredients\nDirections\n5") == "Untitled recipe"
