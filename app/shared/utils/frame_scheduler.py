"""Frame-count alignment helpers.

Models declare their latent packing as a (minimum, step, offset) triple:
a valid frame count is `offset + k * step` for some k, and never below
`minimum`. MiniMax H3, for example, packs 17 frames per latent with a
5-frame offset (107, 124, 141, ... frames).

Ported from upstream WanGP's shared/utils/frame_scheduler.py, limited to
the pure counting helpers — the sliding-window scheduler in upstream
depends on 12.x plumbing Maestro does not carry.
"""

import math


def normalize_frame_count(frame_count: int, minimum: int, step: int, offset: int = 1) -> int:
    """Round `frame_count` UP to the next valid count for this packing."""
    frame_count = max(minimum, frame_count)
    step = max(1, step)
    offset = max(0, offset)
    return math.ceil(max(0, frame_count - offset) / step) * step + offset if step > 1 else frame_count


def floor_frame_count(frame_count: int, minimum: int, step: int, offset: int = 1) -> int:
    """Round `frame_count` DOWN to a valid count, never below `minimum`."""
    frame_count = max(minimum, frame_count)
    step = max(1, step)
    offset = max(0, offset)
    if step <= 1:
        return frame_count
    lower = ((frame_count - offset) // step) * step + offset
    return lower if lower >= minimum else normalize_frame_count(minimum, minimum, step, offset)


def normalize_output_frame_count(frame_count: int, minimum: int, step: int, offset: int = 1) -> int:
    """Round `frame_count` to the NEAREST valid count (ties round down)."""
    frame_count = max(minimum, frame_count)
    step = max(1, step)
    if step <= 1:
        return frame_count
    lower = floor_frame_count(frame_count, minimum, step, offset)
    upper = normalize_frame_count(frame_count, minimum, step, offset)
    return lower if frame_count - lower <= upper - frame_count else upper


__all__ = ["floor_frame_count", "normalize_frame_count", "normalize_output_frame_count"]
