from luminophore_shell.hyprland import SpatialOutputView, SpatialState, SpatialView, SpatialWindow
from luminophore_shell.spatial_edit import SpatialEditPreview


def snapshot():
    return SpatialState(active=True, revision=3, columns=15, rows=5, view=SpatialView(0, 0, 3, 2),
                        windows=(SpatialWindow("0xa", 1, 1, True), SpatialWindow("0xb", 1, 1, True, "floating")),
                        committed=True, topology_revision=2, committed_model_revision=3, committed_topology_revision=2,
                        output_views=(SpatialOutputView(10, SpatialView(0, 0, 3, 2)), SpatialOutputView(20, SpatialView(3, 0, 3, 2))))


def result(status="applied", revision=4, topology=2):
    return SpatialEditPreview(status, revision, topology, (), ())
