"""Shared precondition-skip reason constants for the live and conformance tiers.

The live and conformance gates (``tests/tiers.py``) and the live local-images
skip (``tests/test_live_fixtures.py``) build their skip messages from the
constants here. Fully static messages are whole-string constants; dynamic
messages are built from a fixed prefix (and, for the images-not-local case, a
fixed suffix) around the varying part. The effective text and the conditions
under which a test skips are unchanged.

``scripts/test_report.py`` (task 4.2) imports the same values to reclassify a
precondition skip as "not run" rather than "skipped", so the match is not text
guessing.
"""

from __future__ import annotations

# live tier (tests/tiers.py ``live_tier``).
#
# Skips with the static "disabled" message when the tier is not in SHIM_TIERS,
# and with the "cannot run" prefix plus the runtime reason when the tier is
# enabled but the runtime is unusable.
LIVE_TIER_DISABLED = "live tier disabled: set SHIM_TIERS=live to run live tests"
LIVE_TIER_CANNOT_RUN_PREFIX = "live tier cannot run:"

# live local-images skip (tests/test_live_fixtures.py ``require_images``).
#
# Skips when the image listing fails, or when a pinned image is not already
# local (never pulled). The "images ... not available locally" message is built
# from a fixed prefix and suffix around the (dynamic) list of missing refs.
LIVE_IMAGES_LS_FAILED_PREFIX = "container image ls failed:"
LIVE_IMAGES_NOT_LOCAL_PREFIX = "images "
LIVE_IMAGES_NOT_LOCAL_SUFFIX = " not available locally"

# conformance tier (tests/tiers.py ``conformance_tier``).
#
# Skips with the static "disabled" message when the tier is not in SHIM_TIERS,
# with the static "docker not set" message when SHIM_CONFORMANCE_DOCKER is
# absent, and with the "cannot run" prefix plus the runtime reason when the
# tier is enabled but the runtime is unusable.
CONFORMANCE_TIER_DISABLED = (
    "conformance tier disabled: set SHIM_TIERS=conformance "
    "and SHIM_CONFORMANCE_DOCKER to run conformance tests"
)
CONFORMANCE_DOCKER_NOT_SET = (
    "conformance tier cannot run: SHIM_CONFORMANCE_DOCKER is not set"
)
CONFORMANCE_TIER_CANNOT_RUN_PREFIX = "conformance tier cannot run:"
