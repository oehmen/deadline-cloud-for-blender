# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.

from __future__ import annotations

import logging
import os
from typing import Callable, cast, Optional

import bpy  # type: ignore

_logger = logging.getLogger(__name__)


class DefaultBlenderHandler:
    # Blender 4.2-4.x renamed EEVEE to EEVEE_NEXT, but 5.0+ reverted back to EEVEE
    RENDER_ENGINE_NAME = (
        "BLENDER_EEVEE_NEXT" if (4, 2, 0) <= bpy.app.version < (5, 0, 0) else "BLENDER_EEVEE"
    )

    def __init__(self):
        """Initialize this handler."""

        # Define Blender actions to perform, each keyed to a callback function.
        # These cross-reference the Action instances created and queued in the Blender adaptor.
        self.action_dict = {
            "scene_file": self.set_scene_file,
            "gpu_device": self.set_gpu_device,
            "render_scene": self.set_render_scene,
            "view_layer": self.set_view_layer,
            "camera": self.set_camera,
            "output_dir": self.set_output_dir,
            "output_file_name": self.set_output_file_name,
            "output_format": self.set_output_format,
            "start_render": self.start_render,
        }

        # Set default values.
        self.camera_name = None
        self.scene_name = None
        self.output_dir = None
        self.output_file_name = None
        self.format = None
        self.view_layer_name = ""

        # Optional path-mapping callable, injected by the BlenderClient
        # (ClientInterface.map_path). When set, it takes a path and returns the
        # worker-local path according to the session's path mapping rules. Used
        # to remap the scene's external file references after the file is opened.
        self.map_path: Optional[Callable[[str], str]] = None

        _logger.debug(f"Initialized {self.__class__.__name__}")

    def _ensure_camera(self, data: dict) -> Optional[str]:
        """
        Ensure that the camera provided in `data` exists in the scene and is renderable.
        Raises a RuntimeError otherwise.

        Returns the name of the camera.
        """
        camera: str = cast(str, data.get("camera", self.camera_name))
        if camera is None:
            return None

        # The ls function returns all cameras if they are set to renderable.
        scene_cameras = [
            cam.name for cam in bpy.data.scenes[self.scene_name].objects if cam.type == "CAMERA"
        ]
        if camera not in scene_cameras:
            raise RuntimeError(f"Camera {camera} does not exist.")

        if bpy.data.objects[camera].hide_render:
            raise RuntimeError(f"Camera {camera} is not renderable.")

        _logger.debug(f"Fetched camera to render: {camera}")
        return camera

    def _ensure_view_layer(self) -> None:
        """
        Ensure that the view layer is properly set before rendering.
        """

        if not self.view_layer_name:
            return

        for layer in bpy.context.window.scene.view_layers:
            layer.use = layer.name == self.view_layer_name
            _logger.debug(f"Set layer {layer.name} to render: {layer.use}")

    def start_render(self, data: dict) -> None:
        """
        Starts a render in Blender.

        Args:
            data (dict): The data given from the Adaptor. Keys expected: ['frame', 'camera']

        Raises:
            RuntimeError: If no camera was specified, no renderable camera was found, or required settings weren't provided
        """

        frame = data.get("frame")
        if frame is None:
            raise RuntimeError("BlenderClient: start_render called without a frame number.")

        bpy.context.scene.frame_set(frame)
        _logger.debug(f"Set frame {frame} to render")

        # The camera fetched here is only for logging purposes.
        # The actual camera to render is set in the set_camera function,
        # and should have already been called as an Action.
        camera = self._ensure_camera(data)

        # Add submitter settings to the output filepath.
        # This includes output directory, view layer, camera, and frame.
        try:
            bpy.context.scene.render.filepath = f"{self.output_dir}/{self.view_layer_name}{'_' + camera if camera else ''}_{self.output_file_name}"
        except Exception:
            _logger.error(
                f"Could not set the output file path. Please verify that the following are correct:\nOutput directory: {self.output_dir}\nView layer: {self.view_layer_name}\nCamera:{camera}\nOutput file prefix:{self.output_file_name}"
            )
            raise

        # This API call replaces the # padding with the proper frame number, necessary since `write_still` is True:
        bpy.context.scene.render.filepath = bpy.context.scene.render.frame_path(frame=frame)
        _logger.debug(f"Set output file path to {bpy.context.scene.render.filepath}")

        # We also have to reset the correct layer before rendering each time.
        self._ensure_view_layer()

        _logger.debug(f"Rendering camera: {camera}")
        # To render only single layers, we must set `write_still` to True and `animation` to False (default).
        # See: https://docs.blender.org/api/current/bpy.ops.render.html#bpy.ops.render.render
        bpy.ops.render.render(scene=self.scene_name, layer=self.view_layer_name, write_still=True)

        # This print statement (including flush) is required for Deadline to pick up successful task completion
        # See the Regex callbacks defined at `BlenderAdaptor/adaptor.py:_get_regex_callbacks`.
        print(f"BlenderClient: Finished Rendering Frame {frame}", flush=True)

    def set_camera(self, data: dict) -> None:
        """
        Sets the Camera that will be rendered.

        Args:
            data (dict): The data given from the Adaptor. Keys expected: ['camera']

        Raises:
            RuntimeError: If the camera is not renderable or does not exist
        """
        self.camera_name = data.get("camera")
        camera = self._ensure_camera(data)

        if camera:
            scene = bpy.context.scene
            assert self.camera_name == camera, "Camera name mismatch: this should never happen."
            scene.camera = bpy.data.objects[camera]

            # Iterate through all markers in the scene and set the bound camera to be the selected camera
            for marker in scene.timeline_markers:
                marker.camera = scene.camera

    def set_render_scene(self, data: dict) -> None:
        """
        Sets the Scene that will be rendered and ensures it is set to the
        correct renderer.
        """
        self.scene_name = data.get("render_scene")
        bpy.context.window.scene = bpy.data.scenes[self.scene_name]
        _logger.debug(f"Set render engine: {self.RENDER_ENGINE_NAME}")
        bpy.context.scene.render.engine = self.RENDER_ENGINE_NAME

    def set_output_dir(self, data: dict) -> None:
        """
        Sets the output directory path.

        Args:
            data (dict): The data given from the Adaptor. Keys expected: ['output_dir']
        """
        output_dir = data.get("output_dir")
        if output_dir:
            self.output_dir = output_dir

    def set_output_file_name(self, data: dict) -> None:
        """
        Sets the output file name.

        Args:
            data (dict): The data given from the Adaptor. Keys expected: ['output_file_name']
        """
        output_file_name = data.get("output_file_name", "")
        _logger.debug(f"Set output file name: {output_file_name}")
        if output_file_name:
            self.output_file_name = output_file_name

    def set_output_format(self, data: dict) -> None:
        """
        Sets the output format.
        """
        format_ = data.get("output_format")
        _logger.debug(f"Set output format to {format_}")
        if format_:
            self.format = format_

    def set_view_layer(self, data: dict) -> None:
        """
        Sets the view_layer.

        For Blender, this means disable all layers except the one to render.

        Args:
            data (dict): The data given from the Adaptor. Keys expected: ['view_layer']

        Raises:
            RuntimeError: If the view_layer cannot be found
        """
        view_layer_name = data.get("view_layer")

        if view_layer_name is None:
            raise RuntimeError("View layer was not provided in init data")

        self.view_layer_name = view_layer_name
        self._ensure_view_layer()

    def set_scene_file(self, data: dict):
        """Opens a Blender scene file.

        Args:
            data (dict): The data given from the Adaptor. Keys expected: ['scene_file']

        Raises:
            FileNotFoundError: If the file provided in the data dictionary does not exist.
        """
        file_path = data.get("scene_file", "")
        if not os.path.isfile(file_path):
            raise FileNotFoundError(f"The scene file '{file_path}' does not exist")
        bpy.ops.wm.open_mainfile(filepath=file_path)

        # The scene_file parameter is a PATH job parameter, so Deadline Cloud
        # has already mapped it to its worker-local location. The paths *inside*
        # the scene (linked libraries, images, caches, ...) are stored in the
        # .blend file itself and are NOT job parameters, so Deadline Cloud does
        # not touch them. On a worker whose OS/mounts differ from where the scene
        # was authored (e.g. a Windows "X:\\" path rendered on a Linux worker that
        # mounts the share at "/mnt/projects"), those references do not resolve
        # and the render loses its linked assets and textures. Apply the session's
        # path mapping rules to them here, after the file is open.
        self._remap_external_paths()

    def _remap_external_paths(self) -> None:
        """Remap the open scene's external file references using the adaptor's
        path mapping rules.

        Blender stores asset references (linked libraries, images, caches, sounds,
        movie clips, volumes, fonts) inside the .blend file. These are not Deadline
        Cloud PATH job parameters, so they are not remapped automatically; Blender
        falls back to its own naive drive-letter conversion (e.g. ``X:\\`` -> ``/x/``)
        which does not exist on the worker. This rewrites each absolute reference
        through ``self.map_path`` (the session's path mapping rules) and reloads
        libraries and images so the remapped paths take effect.

        Does nothing if no path mapper was injected. Relative paths (``//...``) and
        paths unchanged by the rules are left untouched, so this is a no-op when the
        scene is already portable or no mapping applies.
        """
        map_path = self.map_path
        if map_path is None:
            _logger.debug("No path mapper available; skipping external path remap.")
            return

        def _remap_one(datablock) -> bool:
            """Remap a single datablock's filepath in place. Returns True if changed."""
            try:
                original = datablock.filepath
            except AttributeError:
                return False
            # Empty, packed, or relative (//) references need no mapping: relative
            # paths resolve against the (already mapped) .blend location on any host.
            if not original or original.startswith("//"):
                return False
            try:
                mapped = map_path(bpy.path.abspath(original))
            except Exception as exc:  # pragma: no cover - mapping is best-effort
                _logger.warning(f"Could not map path '{original}': {exc}")
                return False
            if mapped and mapped != original:
                datablock.filepath = mapped
                _logger.debug(f"Remapped '{original}' -> '{mapped}'")
                return True
            return False

        # Libraries first: linked .blend files must be remapped and reloaded so the
        # data they provide (and any nested library paths) resolve on the worker.
        libraries_changed = False
        for library in bpy.data.libraries:
            if _remap_one(library):
                libraries_changed = True
        if libraries_changed:
            for library in bpy.data.libraries:
                try:
                    library.reload()
                except Exception as exc:  # pragma: no cover - reload is best-effort
                    _logger.warning(f"Could not reload library '{library.filepath}': {exc}")

        # Other external data blocks that carry a filepath.
        for collection in (
            bpy.data.images,
            bpy.data.cache_files,
            bpy.data.sounds,
            bpy.data.movieclips,
            getattr(bpy.data, "volumes", ()),
            bpy.data.fonts,
        ):
            for datablock in collection:
                changed = _remap_one(datablock)
                # Images cache their pixels; force a reload so the mapped file is read.
                if changed and hasattr(datablock, "reload"):
                    try:
                        datablock.reload()
                    except Exception as exc:  # pragma: no cover - reload is best-effort
                        _logger.warning(f"Could not reload datablock '{datablock.filepath}': {exc}")

    def set_gpu_device(self, data: dict) -> None:
        """
        Only implemented in the Cycles handler.
        """
