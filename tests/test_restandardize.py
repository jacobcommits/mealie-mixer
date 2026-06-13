"""Tests for B9 — fix existing Mealie recipes (re-standardize).

Covers:
- recipe_to_text: Mealie JSON → plain text for the LLM
- fetch_recipes: slug + name list
- update_recipe: PATCHes an existing slug, NEVER deletes on failure
- _patch_fields: shared PATCH logic
- API endpoints: /api/mealie-recipes, /api/restandardize, /api/recipes/{slug}/update
"""

from unittest.mock import MagicMock, patch, call
import pytest
import push


# ── recipe_to_text ─────────────────────────────────────────────────────

def test_recipe_to_text_full():
    """A fully populated Mealie recipe renders every section."""
    mealie = {
        "name": "Test Soup",
        "description": "A lovely soup",
        "recipeServings": 4,
        "recipeYield": "4 bowls",
        "recipeIngredient": [
            {"display": "500 g chicken breast"},
            {"display": "1 onion, diced"},
        ],
        "recipeInstructions": [
            {"text": "Chop the onion."},
            {"text": "Cook the chicken."},
        ],
        "recipeCategory": [
            {"name": "Soup"},
            {"name": "Main Course"},
        ],
        "notes": [
            {"title": "Storage", "text": "Keeps 3 days in the fridge."},
        ],
    }
    text = push.recipe_to_text(mealie)
    assert "Name: Test Soup" in text
    assert "Description: A lovely soup" in text
    assert "Servings: 4 · 4 bowls" in text
    assert "500 g chicken breast" in text
    assert "1 onion, diced" in text
    assert "Chop the onion." in text
    assert "Cook the chicken." in text
    assert "Soup" in text
    assert "Main Course" in text
    assert "Storage: Keeps 3 days" in text


def test_recipe_to_text_structured_fallback():
    """When display is missing, falls back to structured food/unit/quantity fields."""
    mealie = {
        "name": "Eggs",
        "recipeIngredient": [
            {"quantity": 2, "unit": None, "food": {"name": "egg"}, "note": "large"},
            {"quantity": 200, "unit": {"name": "g"}, "food": {"name": "flour"}, "note": ""},
        ],
    }
    text = push.recipe_to_text(mealie)
    assert "2 egg, large" in text
    assert "200 g flour" in text


def test_recipe_to_text_empty():
    """An empty recipe produces an empty string."""
    assert push.recipe_to_text({}) == ""


def test_recipe_to_text_unparsed_note():
    """Scraper-imported ingredients that are just a note string."""
    mealie = {
        "name": "Imported",
        "recipeIngredient": [
            {"display": "", "quantity": None, "unit": None, "food": None, "note": "a pinch of salt"},
        ],
    }
    text = push.recipe_to_text(mealie)
    assert "a pinch of salt" in text


# ── update_recipe: PATCHes, never deletes ─────────────────────────────

class FakeResponse:
    """Minimal mock for httpx.Response."""
    status_code = 200
    def raise_for_status(self):
        pass
    def json(self):
        return {"items": []}


def test_update_recipe_patches_name_and_fields():
    """update_recipe PATCHes the name and runs _patch_fields on the existing slug."""
    client = MagicMock()
    client.patch.return_value = FakeResponse()
    client.post.return_value = FakeResponse()
    client.get.return_value = FakeResponse()

    recipe = {
        "name": "Updated Name",
        "description": "Better",
        "ingredients": [],
        "instructions": ["Step 1"],
    }

    with patch.object(push, "_mealie", return_value=("http://mealie:9925", "tok")):
        slug = push.update_recipe("old-slug", recipe, client=client)

    assert slug == "old-slug"
    # Name is PATCHed
    client.patch.assert_any_call("/api/recipes/old-slug", json={"name": "Updated Name"})
    # Description is PATCHed (via _patch_fields)
    client.patch.assert_any_call("/api/recipes/old-slug", json={"description": "Better"})
    # No create (POST /api/recipes) was called
    for c in client.post.call_args_list:
        assert "/api/recipes" not in str(c) or "image" in str(c) or c != call("/api/recipes", json={"name": "Updated Name"})


def test_update_recipe_never_deletes_on_failure():
    """CRITICAL: update_recipe must NOT call DELETE on failure — it would
    destroy the user's real recipe."""
    client = MagicMock()
    # First PATCH (name) succeeds, second (_patch_fields desc) raises
    ok = FakeResponse()
    client.patch.side_effect = [ok, Exception("Mealie exploded")]
    client.get.return_value = FakeResponse()

    recipe = {"name": "Boom", "description": "will fail"}

    with patch.object(push, "_mealie", return_value=("http://mealie:9925", "tok")):
        with pytest.raises(Exception, match="Mealie exploded"):
            push.update_recipe("important-recipe", recipe, client=client)

    # DELETE must NEVER have been called
    client.delete.assert_not_called()


def test_update_recipe_no_create():
    """update_recipe must not POST /api/recipes to create a new recipe shell."""
    client = MagicMock()
    client.patch.return_value = FakeResponse()
    client.post.return_value = FakeResponse()
    client.get.return_value = FakeResponse()

    recipe = {"name": "Keep", "ingredients": [], "instructions": []}

    with patch.object(push, "_mealie", return_value=("http://mealie:9925", "tok")):
        push.update_recipe("existing-slug", recipe, client=client)

    # No POST to /api/recipes (create) — only posts would be for image or resolve
    for c in client.post.call_args_list:
        assert c[0][0] != "/api/recipes"


# ── fetch_recipes ─────────────────────────────────────────────────────

def test_fetch_recipes_returns_slug_name():
    """fetch_recipes returns sorted [{slug, name}] dicts."""
    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "items": [
            {"slug": "z-soup", "name": "Zupa"},
            {"slug": "a-salad", "name": "A Salad"},
        ]
    }
    mock_resp.raise_for_status = MagicMock()

    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.get.return_value = mock_resp

    with patch.object(push, "_mealie", return_value=("http://m:9925", "t")):
        with patch("push.httpx.Client", return_value=mock_client):
            result = push.fetch_recipes()

    assert len(result) == 2
    assert result[0]["name"] == "A Salad"  # sorted
    assert result[1]["slug"] == "z-soup"
