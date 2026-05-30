"""Test setup: neutralise config-bearing env vars BEFORE the app modules are
imported, so config is deterministic and importing `app` doesn't try to reach
Mealie (the food-list load is skipped when Mealie is unset)."""

import os

# Set to "" so python-dotenv (override=False) won't pull values from a real .env,
# and config.get() treats "" as unset → falls through to file/default.
for _k in ("MEALIE_URL", "MEALIE_TOKEN", "AI_API_KEY", "MIXER_AUTH_USER",
           "MIXER_AUTH_PASS", "MIXER_AUTH_PASS_HASH", "MIXER_API_KEY"):
    os.environ[_k] = ""
