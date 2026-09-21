"""Shared Shell widget and PiP edge placement in logical pixels."""

def anchored_panel_bounds(
    side: str,
    viewport_width: int,
    panel_width: int,
    panel_height: int,
    edge_inset: int = 0,
    top_inset: int = 0,
    viewport_height: int | None = None,
    bottom_inset: int = 0,
    vertical: str = "top",
) -> tuple[int, int, int, int]:
    inset = max(0, min(edge_inset, max(0, (viewport_width - 1) // 2)))
    available_width = max(1, viewport_width - 2 * inset)
    bounded_width = max(1, min(available_width, panel_width))
    bounded_height = max(1, panel_height)
    x = viewport_width - inset - bounded_width if side == "right" else inset
    if vertical == "bottom" and viewport_height is not None:
        y = max(0, viewport_height - max(0, bottom_inset) - bounded_height)
    else:
        y = max(0, top_inset)
    return x, y, bounded_width, bounded_height

