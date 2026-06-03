"""Tests for the API layer (Phase 5, Stage 1).

Covers Pydantic model round-trips (especially the ``yield`` alias),
the dict shape that push_recipe expects, and the fail-closed auth
dependency.  No live server or network calls — pure unit tests.
"""

import pytest
from fastapi import HTTPException

import config
from api import Ingredient, Recipe, ExtractResponse, PushResponse, require_api_key


# ── helpers ──────────────────────────────────────────────────────────────

def _cfg(monkeypatch, extras: dict | None = None):
    """Point config at an in-memory file_cfg dict, clearing env vars so
    config.get() reads from _file_cfg only."""
    base = {"AI_API_KEY": "k", "MEALIE_URL": "http://m:9925", "MEALIE_TOKEN": "t"}
    if extras:
        base.update(extras)
    monkeypatch.setattr(config, "_file_cfg", base)
    for k in base:
        monkeypatch.delenv(k, raising=False)


# ── Pydantic model tests ────────────────────────────────────────────────

class TestIngredient:
    def test_round_trip(self):
        ing = Ingredient(quantity=1.5, unit="tbsp", food="butter", note="melted")
        d = ing.model_dump()
        assert d == {"quantity": 1.5, "unit": "tbsp", "food": "butter", "note": "melted", "title": None}

    def test_nulls(self):
        ing = Ingredient(food="salt", note="to taste")
        d = ing.model_dump()
        assert d["quantity"] is None
        assert d["unit"] is None

    def test_empty(self):
        ing = Ingredient()
        d = ing.model_dump()
        assert all(v is None for v in d.values())


class TestRecipe:
    def test_yield_alias_input(self):
        """JSON with 'yield' key maps to recipe_yield in Python."""
        r = Recipe.model_validate({"name": "X", "yield": "4 servings"})
        assert r.recipe_yield == "4 servings"

    def test_yield_alias_output(self):
        """model_dump(by_alias=True) outputs 'yield', not 'recipe_yield'."""
        r = Recipe(name="X", recipe_yield="4 servings")
        d = r.model_dump(by_alias=True)
        assert d["yield"] == "4 servings"
        assert "recipe_yield" not in d

    def test_populate_by_name(self):
        """Can also set recipe_yield by its Python name (populate_by_name)."""
        r = Recipe.model_validate({"name": "X", "recipe_yield": "2 loaves"})
        assert r.recipe_yield == "2 loaves"

    def test_full_round_trip(self):
        """A full recipe dict round-trips through the model unchanged."""
        data = {
            "name": "Classic Egg Spread",
            "description": "A quick spread.",
            "servings": 6,
            "yield": "6 sandwiches",
            "ingredients": [
                {"quantity": 6, "unit": None, "food": "eggs", "note": None},
                {"quantity": 1, "unit": "tbsp", "food": "mayo", "note": "full fat"},
            ],
            "instructions": ["Boil eggs.", "Mix everything."],
            "tags": ["breakfast"],
            "image_url": "https://example.com/photo.jpg",
        }
        r = Recipe.model_validate(data)
        d = r.model_dump(by_alias=True)
        assert d["name"] == "Classic Egg Spread"
        assert d["yield"] == "6 sandwiches"
        assert d["servings"] == 6
        assert len(d["ingredients"]) == 2
        assert d["ingredients"][0]["food"] == "eggs"
        assert d["image_url"] == "https://example.com/photo.jpg"

    def test_defaults(self):
        """Minimal recipe (name only) gets sensible defaults."""
        r = Recipe(name="Minimal")
        d = r.model_dump(by_alias=True)
        assert d["description"] == ""
        assert d["yield"] == ""
        assert d["servings"] is None
        assert d["ingredients"] == []
        assert d["instructions"] == []
        assert d["tags"] == []
        assert d["image_url"] is None


# ── Push handler dict shape ──────────────────────────────────────────────

class TestPushDictShape:
    def test_has_all_keys_push_expects(self):
        """model_dump(by_alias=True) produces every key push_recipe reads."""
        recipe = Recipe.model_validate({
            "name": "Pancakes",
            "servings": 4,
            "yield": "4 servings",
            "ingredients": [
                {"quantity": 200, "unit": "g", "food": "flour", "note": None},
                {"quantity": None, "unit": None, "food": "salt", "note": "to taste"},
            ],
            "instructions": ["Mix.", "Cook."],
        })
        d = recipe.model_dump(by_alias=True)
        # push_recipe accesses these keys:
        for key in ("name", "description", "servings", "yield",
                     "ingredients", "instructions", "tags", "image_url"):
            assert key in d
        assert d["ingredients"][0]["quantity"] == 200
        assert d["ingredients"][1]["food"] == "salt"

    def test_null_image_url(self):
        """image_url absent → None (push_recipe checks truthiness)."""
        d = Recipe(name="X").model_dump(by_alias=True)
        assert d["image_url"] is None


# ── Auth dependency ──────────────────────────────────────────────────────

class TestAuth:
    def test_503_when_api_key_empty(self, monkeypatch):
        """Fail-closed: empty MIXER_API_KEY → 503 (API disabled)."""
        _cfg(monkeypatch)  # no MIXER_API_KEY
        with pytest.raises(HTTPException) as exc_info:
            require_api_key(authorization="Bearer whatever")
        assert exc_info.value.status_code == 503
        assert "disabled" in exc_info.value.detail.lower()

    def test_503_when_app_unconfigured(self, monkeypatch):
        """API key set but app not configured → 503."""
        monkeypatch.setattr(config, "_file_cfg", {"MIXER_API_KEY": "my-key"})
        # AI_API_KEY / MEALIE_URL / MEALIE_TOKEN are all "" → unconfigured
        for k in ("AI_API_KEY", "MEALIE_URL", "MEALIE_TOKEN", "MIXER_API_KEY"):
            monkeypatch.delenv(k, raising=False)
        with pytest.raises(HTTPException) as exc_info:
            require_api_key(authorization="Bearer my-key")
        assert exc_info.value.status_code == 503
        assert "not configured" in exc_info.value.detail.lower()

    def test_401_wrong_key(self, monkeypatch):
        """Wrong key → 401."""
        _cfg(monkeypatch, {"MIXER_API_KEY": "correct"})
        with pytest.raises(HTTPException) as exc_info:
            require_api_key(authorization="Bearer wrong")
        assert exc_info.value.status_code == 401

    def test_401_missing_key(self, monkeypatch):
        """No key at all → 401."""
        _cfg(monkeypatch, {"MIXER_API_KEY": "correct"})
        with pytest.raises(HTTPException) as exc_info:
            require_api_key(authorization=None, x_api_key=None)
        assert exc_info.value.status_code == 401

    def test_401_bearer_prefix_only(self, monkeypatch):
        """'Bearer ' with nothing after it → 401."""
        _cfg(monkeypatch, {"MIXER_API_KEY": "correct"})
        with pytest.raises(HTTPException) as exc_info:
            require_api_key(authorization="Bearer ")
        assert exc_info.value.status_code == 401

    def test_passes_bearer(self, monkeypatch):
        """Correct Bearer token → returns the key."""
        _cfg(monkeypatch, {"MIXER_API_KEY": "my-secret"})
        result = require_api_key(authorization="Bearer my-secret")
        assert result == "my-secret"

    def test_passes_x_api_key(self, monkeypatch):
        """X-API-Key header → returns the key."""
        _cfg(monkeypatch, {"MIXER_API_KEY": "my-secret"})
        result = require_api_key(authorization=None, x_api_key="my-secret")
        assert result == "my-secret"

    def test_bearer_takes_priority(self, monkeypatch):
        """When both headers are present, Bearer wins."""
        _cfg(monkeypatch, {"MIXER_API_KEY": "bearer-val"})
        result = require_api_key(
            authorization="Bearer bearer-val", x_api_key="other-val",
        )
        assert result == "bearer-val"


# ── Response models ──────────────────────────────────────────────────────

class TestResponseModels:
    def test_extract_response(self):
        r = ExtractResponse(recipes=[Recipe(name="A"), Recipe(name="B")])
        d = r.model_dump(by_alias=True)
        assert len(d["recipes"]) == 2
        assert d["recipes"][0]["name"] == "A"

    def test_push_response(self):
        r = PushResponse(slug="classic-egg-spread", url="http://m:9925/g/home/r/classic-egg-spread")
        assert r.slug == "classic-egg-spread"
        assert "classic-egg-spread" in r.url
