from unittest.mock import MagicMock, patch

import httpx
import pytest

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


def test_extract_items():
    """_extract_items handles both paginated dicts and raw lists safely."""
    assert push._extract_items({"items": [{"name": "A"}]}) == [{"name": "A"}]
    assert push._extract_items([{"name": "B"}]) == [{"name": "B"}]
    assert push._extract_items(None) == []
    assert push._extract_items("invalid") == []


def test_clean_category():
    """_clean_category strips internal/read-only fields, keeping only id, name, slug."""
    raw = {
        "id": "cat-123",
        "name": "Dinner",
        "slug": "dinner",
        "createdAt": "2026-01-01",
        "updatedAt": "2026-01-02",
        "userId": "user-456",
        "recipes": ["r1", "r2"],
    }
    cleaned = push._clean_category(raw)
    assert cleaned == {"id": "cat-123", "name": "Dinner", "slug": "dinner"}


def test_clean_tag():
    """_clean_tag strips internal/read-only fields, keeping only id, name, slug."""
    raw = {
        "id": "tag-123",
        "name": "Quick",
        "slug": "quick",
        "createdAt": "2026-01-01",
        "updatedAt": "2026-01-02",
        "userId": "user-456",
        "recipes": ["r1"],
    }
    cleaned = push._clean_tag(raw)
    assert cleaned == {"id": "tag-123", "name": "Quick", "slug": "quick"}


def test_format_mealie_error():
    """_format_mealie_error formats HTTPStatusError with status code and response body."""
    request = httpx.Request("POST", "http://localhost/api/recipes")
    response = httpx.Response(400, text='{"detail":"Invalid payload"}', request=request)
    http_err = httpx.HTTPStatusError("400 Bad Request", request=request, response=response)

    formatted = push._format_mealie_error(http_err)
    assert isinstance(formatted, RuntimeError)
    assert "Mealie HTTP 400" in str(formatted)
    assert '{"detail":"Invalid payload"}' in str(formatted)


def test_patch_fields_sanitizes_categories_and_tags():
    """_patch_fields resolves categories & tags and sends clean category & tag dicts."""
    client = MagicMock()

    # Mock responses for _load_lookup and _resolve
    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "items": [
            {
                "id": "c1",
                "name": "Main Course",
                "slug": "main-course",
                "recipes": ["r1"],
                "createdAt": "2026-01-01",
            }
        ]
    }
    mock_resp.raise_for_status = MagicMock()

    tag_resp = MagicMock()
    tag_resp.json.return_value = {
        "items": [
            {
                "id": "t1",
                "name": "Quick",
                "slug": "quick",
                "recipes": ["r1"],
                "createdAt": "2026-01-01",
            }
        ]
    }
    tag_resp.raise_for_status = MagicMock()

    # Handle client.get calls for /api/organizers/categories vs /api/organizers/tags
    def get_side_effect(url, **kwargs):
        if "tags" in url:
            return tag_resp
        return mock_resp

    client.get.side_effect = get_side_effect
    client.patch.return_value = mock_resp

    recipe = {"categories": ["Main Course"], "tags": ["Quick"]}
    push._patch_fields(client, "test-slug", recipe)

    # Verify PATCH for recipeCategory carried cleaned category object without extra fields
    client.patch.assert_any_call(
        "/api/recipes/test-slug",
        json={"recipeCategory": [{"id": "c1", "name": "Main Course", "slug": "main-course"}]},
    )
    # Verify PATCH for tags carried cleaned tag object without extra fields
    client.patch.assert_any_call(
        "/api/recipes/test-slug",
        json={"tags": [{"id": "t1", "name": "Quick", "slug": "quick"}]},
    )


def test_patch_fields_clears_categories_and_tags_when_empty():
    """When recipe dict has categories: [], tags: [], _patch_fields sends recipeCategory: [], tags: []."""
    client = MagicMock()
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    client.patch.return_value = mock_resp

    recipe = {"categories": [], "tags": []}
    push._patch_fields(client, "test-slug", recipe)

    client.patch.assert_any_call(
        "/api/recipes/test-slug",
        json={"recipeCategory": []},
    )
    client.patch.assert_any_call(
        "/api/recipes/test-slug",
        json={"tags": []},
    )
