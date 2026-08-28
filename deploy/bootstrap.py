"""Reconcile the volume's config.yaml with the image's, before the server starts.

config.yaml has two kinds of content with opposite needs:

  * the search criteria (budget, suburbs, bedrooms) — the review page writes
    these back into the file, so they must survive a redeploy, which means
    living on the mounted volume;
  * the scrape selectors, JSON paths and outcome mappings — these are the
    thing you edit when a source breaks, so they must come from the image.

So the image's config wins for every byte, and the criteria the settings
panel owns are lifted off the previous volume copy and re-applied on top.
That reuses settings.read_settings / write_settings, which already restrict
themselves to the whitelisted fields and validate before writing — nothing
else from the old file can leak forward.
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from passedin.settings import read_settings, write_settings  # noqa: E402

IMAGE_CONFIG = Path(__file__).resolve().parent.parent / "config.yaml"
LIVE_CONFIG = Path(os.environ.get("PASSEDIN_CONFIG", "/data/config.yaml"))


def main() -> int:
    LIVE_CONFIG.parent.mkdir(parents=True, exist_ok=True)

    if not LIVE_CONFIG.exists():
        shutil.copy2(IMAGE_CONFIG, LIVE_CONFIG)
        print(f"bootstrap: seeded {LIVE_CONFIG} from the image")
        return 0

    try:
        saved = read_settings(LIVE_CONFIG)
    except Exception as e:
        # A corrupt volume config must not take the server down: fall back to
        # the image's criteria rather than refusing to boot.
        print(f"bootstrap: could not read saved criteria ({e}); "
              f"using the image's", file=sys.stderr)
        shutil.copy2(IMAGE_CONFIG, LIVE_CONFIG)
        return 0

    # read_settings returns {values, options, config_path, suburbs_mode};
    # only "values" is writable, and its keys are exactly the whitelist
    # write_settings accepts.
    criteria = dict(saved.get("values") or {})

    shutil.copy2(IMAGE_CONFIG, LIVE_CONFIG)
    # write_settings only rewrites the lines for the keys it is given, so the
    # image's comments and every non-criteria setting pass through untouched.
    try:
        write_settings(LIVE_CONFIG, criteria)
    except Exception as e:
        print(f"bootstrap: could not re-apply saved criteria ({e}); "
              f"the image's defaults are in effect", file=sys.stderr)
        return 0
    print(f"bootstrap: refreshed {LIVE_CONFIG} from the image, "
          f"kept {len(criteria)} saved criteria")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
