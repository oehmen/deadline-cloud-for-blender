# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
"""Unit tests for compositor File Output node capture in the Blender submitter.

`bpy` is stubbed by this package's __init__.py, so scenes and nodes are built
from SimpleNamespace objects to behave like real Blender data (missing
attributes really are missing).
"""

import os
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import patch

SUBMITTER_DIR = (
    Path(__file__).parent.parent.parent.parent
    / "src"
    / "deadline"
    / "blender_submitter"
    / "addons"
    / "deadline_cloud_blender_submitter"
)
sys.path.append(str(SUBMITTER_DIR))

import blender_utils as bu
from deadline.blender_submitter.addons.deadline_cloud_blender_submitter.template_filling import (
    _add_compositor_output_parameters,
    get_parameter_values,
)


def _node(name, directory=None, base_path=None, mute=False, bl_idname="CompositorNodeOutputFile"):
    node = SimpleNamespace(name=name, bl_idname=bl_idname, mute=mute)
    if directory is not None:
        node.directory = directory
    if base_path is not None:
        node.base_path = base_path
    return node


def _get_outputs(scene):
    with (
        patch("blender_utils.bpy.data.scenes", {"Scene": scene}),
        patch("blender_utils.bpy.path.abspath", side_effect=lambda p: p),
    ):
        return bu.get_compositor_outputs("Scene")


class TestGetCompositorOutputs:
    def test_blender_4_node_tree(self, tmp_path):
        """Blender 4.x exposes the compositor as scene.node_tree with base_path."""
        full = str(tmp_path / "renders" / "8k")
        proxy = str(tmp_path / "renders" / "proxy")
        scene = SimpleNamespace(
            use_nodes=True,
            node_tree=SimpleNamespace(
                nodes=[
                    _node("Full", base_path=full),
                    _node("Proxy", base_path=proxy),
                ]
            ),
        )

        outputs = _get_outputs(scene)

        assert [(o.node_name, o.base_path) for o in outputs] == [
            ("Full", os.path.normpath(full)),
            ("Proxy", os.path.normpath(proxy)),
        ]

    def test_blender_5_compositing_node_group(self, tmp_path):
        """Blender 5.0 renamed the tree to scene.compositing_node_group and uses directory."""
        out = str(tmp_path / "out")
        scene = SimpleNamespace(
            use_nodes=True,
            compositing_node_group=SimpleNamespace(nodes=[_node("Out", directory=out)]),
        )

        outputs = _get_outputs(scene)

        assert [(o.node_name, o.base_path) for o in outputs] == [("Out", os.path.normpath(out))]

    def test_skips_muted_non_output_and_pathless_nodes(self, tmp_path):
        keep = str(tmp_path / "keep")
        scene = SimpleNamespace(
            use_nodes=True,
            node_tree=SimpleNamespace(
                nodes=[
                    _node("Muted", base_path=str(tmp_path / "muted"), mute=True),
                    _node("Scale", base_path=str(tmp_path / "x"), bl_idname="CompositorNodeScale"),
                    _node("NoPath", base_path=""),
                    _node("Keep", base_path=keep),
                ]
            ),
        )

        outputs = _get_outputs(scene)

        assert [o.node_name for o in outputs] == ["Keep"]

    def test_compositing_disabled_returns_empty(self, tmp_path):
        scene = SimpleNamespace(
            use_nodes=False,
            node_tree=SimpleNamespace(nodes=[_node("Out", base_path=str(tmp_path))]),
        )

        assert _get_outputs(scene) == []

    def test_no_node_tree_returns_empty(self):
        scene = SimpleNamespace(use_nodes=True, node_tree=None)

        assert _get_outputs(scene) == []


class TestCompositorJobParameters:
    OUTPUTS = [
        {"node_name": "Full", "base_path": "/renders/8k"},
        {"node_name": "Proxy", "base_path": "/renders/proxy"},
    ]

    def test_parameter_definitions_are_output_directories(self):
        job_template: dict = {"parameterDefinitions": []}

        _add_compositor_output_parameters(job_template, self.OUTPUTS)

        defs = job_template["parameterDefinitions"]
        assert [d["name"] for d in defs] == ["CompositorOutputDir0", "CompositorOutputDir1"]
        for d in defs:
            assert d["type"] == "PATH"
            assert d["objectType"] == "DIRECTORY"
            assert d["dataFlow"] == "OUT"
        assert "'Proxy'" in defs[1]["description"]

    def test_parameter_values_carry_node_directories(self):
        settings = SimpleNamespace(
            project_path="/proj/scene.blend",
            gpu_device="NONE",
            compositor_outputs=self.OUTPUTS,
            ocio_config_path="",
            include_adaptor_wheels=False,
        )
        layer_settings = SimpleNamespace(
            output_file_prefix="frame_####",
            output_directories="/renders",
            scene_name="Scene",
            renderer_name="cycles",
            frames_parameter_name=None,
            image_width_parameter_name=None,
            image_height_parameter_name=None,
        )

        params = get_parameter_values(cast(Any, settings), cast(Any, layer_settings), [])

        values = {p["name"]: p["value"] for p in params}
        assert values["CompositorOutputDir0"] == "/renders/8k"
        assert values["CompositorOutputDir1"] == "/renders/proxy"

    def test_no_compositor_outputs_adds_nothing(self):
        job_template: dict = {"parameterDefinitions": []}

        _add_compositor_output_parameters(job_template, [])

        assert job_template["parameterDefinitions"] == []
