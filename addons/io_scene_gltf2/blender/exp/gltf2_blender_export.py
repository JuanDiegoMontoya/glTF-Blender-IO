# Copyright 2018-2021 The glTF-Blender-IO authors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import os
import subprocess
import time

import bpy
import sys
import traceback

from ...io.com.gltf2_io_debug import print_console, print_newline
from ...io.exp import gltf2_io_export
from ...io.exp import gltf2_io_draco_compression_extension
from ...io.exp.gltf2_io_user_extensions import export_user_extensions
from ..com import gltf2_blender_json
from . import gltf2_blender_gather
from .gltf2_blender_gltf2_exporter import GlTF2Exporter


def save(context, export_settings):
    """Start the glTF 2.0 export and saves to content either to a .gltf or .glb file."""
    if bpy.context.active_object is not None:
        if bpy.context.active_object.mode != "OBJECT": # For linked object, you can't force OBJECT mode
            bpy.ops.object.mode_set(mode='OBJECT')

    original_frame = bpy.context.scene.frame_current
    if not export_settings['gltf_current_frame']:
        bpy.context.scene.frame_set(0)

    __notify_start(context)
    start_time = time.time()
    pre_export_callbacks = export_settings["pre_export_callbacks"]
    for callback in pre_export_callbacks:
        callback(export_settings)

    json, buffer = __export(export_settings)

    post_export_callbacks = export_settings["post_export_callbacks"]
    for callback in post_export_callbacks:
        callback(export_settings)
    __write_file(json, buffer, export_settings)

    end_time = time.time()
    __notify_end(context, end_time - start_time)

    if not export_settings['gltf_current_frame']:
        bpy.context.scene.frame_set(int(original_frame))
    return {'FINISHED'}


def __export(export_settings):
    exporter = GlTF2Exporter(export_settings)
    __gather_gltf(exporter, export_settings)
    buffer = __create_buffer(exporter, export_settings)
    exporter.finalize_images()

    export_user_extensions('gather_gltf_extensions_hook', export_settings, exporter.glTF)
    exporter.traverse_extensions()

    # now that addons possibly add some fields in json, we can fix in needed
    json = __fix_json(exporter.glTF.to_dict())

    return json, buffer


def __gather_gltf(exporter, export_settings):
    active_scene_idx, scenes, animations = gltf2_blender_gather.gather_gltf2(export_settings)

    unused_skins = export_settings['vtree'].get_unused_skins()

    if export_settings['gltf_draco_mesh_compression']:
        gltf2_io_draco_compression_extension.encode_scene_primitives(scenes, export_settings)
        exporter.add_draco_extension()

    export_user_extensions('gather_gltf_hook', export_settings, active_scene_idx, scenes, animations)

    for idx, scene in enumerate(scenes):
        exporter.add_scene(scene, idx==active_scene_idx)
    for animation in animations:
        exporter.add_animation(animation)
    exporter.traverse_unused_skins(unused_skins)


def __create_buffer(exporter, export_settings):
    buffer = bytes()
    if export_settings['gltf_format'] == 'GLB':
        buffer = exporter.finalize_buffer(export_settings['gltf_filedirectory'], is_glb=True)
    else:
        if export_settings['gltf_format'] == 'GLTF_EMBEDDED':
            exporter.finalize_buffer(export_settings['gltf_filedirectory'])
        else:
            exporter.finalize_buffer(export_settings['gltf_filedirectory'],
                                     export_settings['gltf_binaryfilename'])

    return buffer


def __fix_json(obj):
    # TODO: move to custom JSON encoder
    fixed = obj
    if isinstance(obj, dict):
        fixed = {}
        for key, value in obj.items():
            if key == 'extras' and value is not None:
                fixed[key] = value
                continue
            if not __should_include_json_value(key, value):
                continue
            fixed[key] = __fix_json(value)
    elif isinstance(obj, list):
        fixed = []
        for value in obj:
            fixed.append(__fix_json(value))
    elif isinstance(obj, float):
        # force floats to int, if they are integers (prevent INTEGER_WRITTEN_AS_FLOAT validator warnings)
        if int(obj) == obj:
            return int(obj)
    return fixed


def __should_include_json_value(key, value):
    allowed_empty_collections = ["KHR_materials_unlit"]

    if value is None:
        return False
    elif __is_empty_collection(value) and key not in allowed_empty_collections:
        return False
    return True


def __is_empty_collection(value):
    return (isinstance(value, dict) or isinstance(value, list)) and len(value) == 0

def __postprocess_with_gltfpack(export_settings):

    gltfpack_path = bpy.context.preferences.addons['io_scene_gltf2'].preferences.gltfpack_path_ui
    gltfpack_binary_file_path = os.path.join(gltfpack_path, "gltfpack")

    gltf_file_path = export_settings['gltf_filepath']
    gltf_file_base = os.path.splitext(os.path.basename(gltf_file_path))[0]
    gltf_file_extension = os.path.splitext(os.path.basename(gltf_file_path))[1]
    gltf_file_directory = os.path.dirname(gltf_file_path)
    gltf_output_file_directory = os.path.join(gltf_file_directory, "gltfpacked")
    if (os.path.exists(gltf_output_file_directory) is False):
        os.makedirs(gltf_output_file_directory)

    gltf_input_file_path = gltf_file_path
    gltf_output_file_path = os.path.join(gltf_output_file_directory, gltf_file_base + gltf_file_extension)

    options = []

    if (export_settings['gltf_gltfpack_tc']):
        options.append("-tc")
    
    options.append("-tq")
    options.append(f"{export_settings['gltf_gltfpack_tq']}")

    options.append("-si")
    options.append(f"{export_settings['gltf_gltfpack_si']}")

    if (export_settings['gltf_gltfpack_sa']):
        options.append("-sa")

    if (export_settings['gltf_gltfpack_slb']):
        options.append("-slb")

    options.append("-vp")
    options.append(f"{export_settings['gltf_gltfpack_vp']}")
    options.append("-vt")
    options.append(f"{export_settings['gltf_gltfpack_vt']}")
    options.append("-vn")
    options.append(f"{export_settings['gltf_gltfpack_vn']}")
    options.append("-vc")
    options.append(f"{export_settings['gltf_gltfpack_vc']}")
    
    match export_settings['gltf_gltfpack_vpi']:
        case "Integer":
            options.append("-vpi")
        case "Normalized":
            options.append("-vpn")
        case "Floating-point":
            options.append("-vpf")

    if (export_settings['gltf_gltfpack_noq']):
        options.append("-noq")

    parameters = []
    parameters.append("-i")
    parameters.append(gltf_input_file_path)
    parameters.append("-o")
    parameters.append(gltf_output_file_path)

    try:
        subprocess.run([gltfpack_binary_file_path] + options + parameters, check=True)
    except subprocess.CalledProcessError as e:
        print_console('ERROR', "Calling gltfpack was not successful")

def __write_file(json, buffer, export_settings):
    try:
        gltf2_io_export.save_gltf(
            json,
            export_settings,
            gltf2_blender_json.BlenderJSONEncoder,
            buffer)
        if (export_settings['gltf_use_gltfpack'] == True):
            __postprocess_with_gltfpack(export_settings)
        
    except AssertionError as e:
        _, _, tb = sys.exc_info()
        traceback.print_tb(tb)  # Fixed format
        tb_info = traceback.extract_tb(tb)
        for tbi in tb_info:
            filename, line, func, text = tbi
            print_console('ERROR', 'An error occurred on line {} in statement {}'.format(line, text))
        print_console('ERROR', str(e))
        raise e


def __notify_start(context):
    print_console('INFO', 'Starting glTF 2.0 export')
    context.window_manager.progress_begin(0, 100)
    context.window_manager.progress_update(0)


def __notify_end(context, elapsed):
    print_console('INFO', 'Finished glTF 2.0 export in {} s'.format(elapsed))
    context.window_manager.progress_end()
    print_newline()
