# ##### BEGIN MIT LICENSE BLOCK #####
#
# blender/io_scene_m3d.py
#
# Copyright (C) 2019 - 2022 bzt (bztsrc@gitlab)
#
# Permission is hereby granted, free of charge, to any person
# obtaining a copy of this software and associated documentation
# files (the "Software"), to deal in the Software without
# restriction, including without limitation the rights to use, copy,
# modify, merge, publish, distribute, sublicense, and/or sell copies
# of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be
# included in all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
# EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
# MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
# NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT
# HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY,
# WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER
# DEALINGS IN THE SOFTWARE.
#
# @brief Blender 2.80 Model 3D Exporter (and one day Importer too)
# https://gitlab.com/bztsrc/model3d
#
# ##### END MIT LICENSE BLOCK #####

# <pep8-80 compliant>

bl_info = {
    "name": "Model 3D (.m3d) format",
    "author": "Dave-bzt",
    "version": (0, 2),
    "blender": (2, 80, 0),
    "location": "File > Import-Export",
    "description": "Export M3D",
    "wiki_url": "https://gitlab.com/bztsrc/model3d/blob/master/docs/m3d_format.md",
    "category": "Import-Export"
}

# -----------------------------------------------------------------------------
# Import libraries
import time
import zlib
import bmesh
import os
from operator import itemgetter
from struct import pack, unpack
from mathutils import Matrix
from bpy_extras import io_utils, node_shader_utils

# -----------------------------------------------------------------------------
#  Blender material property and M3D property type assignments
#  See https://gitlab.com/bztsrc/model3d/blob/master/docs/m3d_format.md section Materials)
mat_property_map = {
    # type  format   PrincipledBSDF property  ASCII variant
    0: ["color", "base_color", "Kd"],
    1: ["gscale", "metallic", "Ka"],
    2: ["gscale", "specular", "Ks"],
    3: ["color", "specular_tint", "Ns"],
    4: ["//color", "emissive", "Ke"],  # not in BSDF?
    5: ["gscale", "transmission", "Tf"],
    6: ["float", "normalmap_strength", "Km"],
    7: ["float", "alpha", "d"],
    8: ["//byte", "illumination", "il"],  # not in PBR at all
    64: ["float", "roughness", "Pr"],
    65: ["float", "metallic", "Pm"],
    66: ["//float", "sheen", "Ps"],  # not in BSDF?
    67: ["float", "ior", "Ni"],
    128: ["map", "base_color_texture", "map_Kd"],
    130: ["//map", "specular_texture", "map_Ks"],  # should work, but it does not
    133: ["map", "transmission_texture", "map_Tf"],
    134: ["map", "normalmap_texture", "map_Km"],
    135: ["map", "alpha_texture", "map_D"],
    192: ["map", "roughness_texture", "map_Pr"],
    193: ["map", "metallic_texture", "map_Pm"],
    195: ["map", "ior_texture", "map_Ni"],
}


# -----------------------------------------------------------------------------
# Load and parse a Model 3D file (this is WIP)
def read_m3d(context,
             filepath,
             report,
             global_matrix=None,
             ):

    return {'FINISHED'}


# -----------------------------------------------------------------------------
# Construct and save a Model 3D file
def write_m3d(context,
              filepath,
              report,
              *,
              use_name='',  # model's name
              use_license='MIT',  # model's license
              use_author='',  # model's author
              use_comment='',  # model's comment
              use_scale=1.0,  # model-space 1.0 in SI meters
              use_selection=True,  # export selected items only
              use_mesh_modifiers=True,  # apply mesh modifiers
              use_normals=False,  # save normal vectors too
              use_uvs=True,  # save texture map UV coordinates
              allow_unnormalized_uvs=False,
              use_colors=True,  # save per vertex colors
              use_materials=True,  # save materials
              use_skeleton=True,  # save bind-pose armature
              use_animation=True,  # save skeletal animations
              use_markers=False,  # use timeline markers for animations
              use_fps=25,  # frame per second
              use_quality='-1',  # -1: auto, 0: 8 bit, 1: 16 bit, 2: 32 bit, 3: 64 bit
              use_inline=False,  # inline textures
              use_gridcompress=True,  # use lossy grid compression
              use_strmcompress=True,  # use lossless stream compression
              use_ascii=False,  # save ASCII variant
              use_relbones=True,  # (debug only) use parent relative bone positions
              global_matrix=None,  # default orientation
              check_existing=True,
              ):
    # convert string to name identifier
    def safestr(name, morelines=0):
        if name is None:
            return ''
        elif morelines == 3:
            return name.replace('\r', '').strip()
        elif morelines == 2:
            return name.replace('\r', '').replace('\n', ' ').strip()
        elif morelines == 1:
            return name.replace('\r', '').replace('\n', '\r\n').strip()
        else:
            return name.replace(' ', '_').replace('/', '_').replace('\\', '_').replace('\r', '').replace('\n',
                                                                                                         ' ').strip()

    # set is unique, but has no index, list has index, but not unique...
    # this is utterly and painfully slow, hence the dict wrapper below
    def uniquelist(l, e):
        try:
            i = l.index(e)
        except ValueError:
            i = len(l)
            l.append(e)
        return i

    # use hash table and then convert dict to list instead
    # this uses considerably more memory, but we have no choice:
    # using uniquelist takes several minutes with 50000 triangles...
    def uniquedict(l, e):
        h = hash(str(e))
        try:
            return l[h][0]
        except KeyError:
            i = len(l)
            l[h] = [i, e]
        return i

    def dict2list(l):
        r = []
        for i, v in l.items():
            r.insert(v[0], v[1])
        return r

    # get index size (we use -1 and -2 as special indices)
    def idxsize(cnt):
        if cnt == 0:
            return 3
        elif cnt < 254:
            return 0
        elif cnt < 65534:
            return 1
        return 2

    # write out an index
    def addidx(fmt, idx):
        # we rely on the fact that in C -1 is a full binary 1 which
        # gives the maximum unsigned value regardless to size, but
        # pack stops us from taking advantage of that
        if fmt == 0:
            if idx < 0:
                idx = 256 + idx
            return pack("<B", idx)
        elif fmt == 1:
            if idx < 0:
                idx = 65536 + idx
            return pack("<H", idx)
        elif fmt == 2:
            if idx < 0:
                idx = 4294967296 + idx
            return pack("<I", idx)
        return b''

    # eliminate minus zero
    def vert(x, y, z, w, c, s):
        if x == -0.0:
            x = 0.0
        if y == -0.0:
            y = 0.0
        if z == -0.0:
            z = 0.0
        if w == -0.0:
            w = 0.0
        return [x, y, z, w, c, s]

    # normalize matrix, decompose and recompose to eliminate errors
    def matnorm(a):
        p, q, s = a.decompose()
        q.normalize()
        return Matrix.Translation(p) @ q.to_matrix().to_4x4()

    def img_to_png(image):
        width = image.size[0]
        height = image.size[1]
        buf = bytearray([int(p * 255) for p in image.pixels])

        # reverse the vertical line order and add null bytes at the start
        width_byte_4 = width * 4
        raw_data = b''.join(b'\x00' + buf[span:span + width_byte_4]
                            for span in range((height - 1) * width_byte_4, -1, - width_byte_4))

        def png_pack(png_tag, data):
            chunk_head = png_tag + data
            return (pack("!I", len(data)) +
                    chunk_head +
                    pack("!I", 0xFFFFFFFF & zlib.crc32(chunk_head)))

        png_bytes = b''.join([
            b'\x89PNG\r\n\x1a\n',
            png_pack(b'IHDR', pack("!2I5B", width, height, 8, 6, 0, 0, 0)),
            png_pack(b'IDAT', zlib.compress(raw_data, 1)),
            png_pack(b'IEND', b'')])

        return png_bytes

    def get_texturedata(node_image, use_inline):
        # NOTE: filepath on image could be relative (starting with //) or already absolute path
        image_path = bpy.path.abspath(node_image.filepath)

        if use_inline:
            # Check if the image is packed in Blender
            if node_image.packed_file is not None:
                if node_image.file_format == "PNG":
                    return node_image.packed_file.data
                else:
                    print("Texture ", node_image.name + " is not a png. Converting...")
                    png_data = img_to_png(node_image)
                    return png_data

            # If not packed, try reading from the file system
            if image_path != "":
                try:
                    with open(image_path, 'rb') as file:
                        data = file.read()

                    if len(data) < 8 or data[0:4] != b'\x89PNG':
                        report({"ERROR"}, f"Texture file '{node_image}' not a valid PNG. Cannot be inlined.")
                        return b''
                    else:
                        return data
                except:
                    # could not find file
                    pass

            report({"ERROR"}, f"Texture file '{node_image.name}' not found. Cannot be inlined.")
        return b''

    # recursively walk skeleton and construct string representation
    def bonestr(strs, bones, parent, level):
        ret = ""
        for i, b in enumerate(bones):
            if b[0] == parent:
                ret += "/" * level + str(b[2]) + " " + str(b[3]) + " " + strs[b[1]] + "\r\n"
                ret += bonestr(strs, bones, i, level + 1)
        return ret

    # the main function execution block
    if True:
        # track time (just for debug)
        time_start = time.time()

        bpy.context.window_manager.progress_begin(0, 100)

        if global_matrix is None:
            global_matrix = axis_conversion(from_forward='-Y', from_up='Z', to_forward='Z', to_up='Y').to_4x4()
        if use_animation:
            use_skeleton = True
        if use_fps < 1 or use_fps > 120:
            use_fps = 25

        # Get Blender objects to export
        scene = context.scene
        if use_selection:
            objects = context.selected_objects
        else:
            objects = context.scene.objects

        # if use_quality is set to auto, then count the number of triangles to decide
        use_quality = int(use_quality)
        if use_quality < 0 or use_quality > 3:
            n = 0
            for i, ob_main in enumerate(objects):
                if ob_main.parent and ob_main.parent.instance_type in {'VERTS', 'FACES'}:
                    continue
                if ob_main.type == 'MESH':
                    n += len(ob_main.data.polygons)
            if n < 1024:
                use_quality = 0
            else:
                use_quality = 1
        # we must use floating point without grid compression
        if use_gridcompress == False and use_quality < 2:
            use_quality = 2
        # get the number of significant digits depending on quality
        if use_quality == 3:
            digits = 15
        if use_quality == 2:
            digits = 7
        else:
            digits = 4

        # Build global lists with unique elements
        # we use a dict wrapper to speed up things
        cmap = {}  # color map entries
        strs = {}  # string table with unique strings
        verts = {}  # unique list of vertices
        tmaps = {}  # texture map UV coordinates
        faces = []  # triangles list
        labels = []  # annotation labels
        materials = []  # translated material name and properties
        bones = {}  # bind-pose skeleton
        skins = {}  # array of bone id / weight combinations per vertex
        actions = []  # animations
        inlined = {}  # inlined textures
        extras = []  # extra chunks (engine specific)

        # ----------------- Start of Blender Specific Stuff ---------------------
        refmats = {}  # unique list of referenced Blender material objects
        nb_m = 0  # maximum number of bone weights per vertex
        fi_m = 0  # frame index maximum

        # set rest armature (bind-pose skeleton)
        # if we don't do this, we'll get strange bones and distorted mesh
        oldaction = None
        oldframe = context.scene.frame_current
        oldpose = {}
        for i, ob_main in enumerate(objects):
            if ob_main.type == "ARMATURE":
                oldpose[i] = ob_main.data.pose_position
                ob_main.data.pose_position = "REST"
                ob_main.data.update_tag()
                if oldaction is None and ob_main.animation_data and ob_main.animation_data.action:
                    oldaction = ob_main.animation_data.action
        context.scene.frame_set(0)

        ### Armature ###
        if use_skeleton:
            # this must be done before the mesh so that skin can refer to bones
            idx = 0
            for i, ob_main in enumerate(objects):
                if ob_main.type != "ARMATURE":
                    continue
                for b in ob_main.data.bones:
                    m = matnorm(global_matrix @ ob_main.matrix_world @ b.matrix_local)
                    a = -1
                    if b.parent:
                        # is there a better way to get the parent's
                        # index in the armature's bone collection?
                        for j, p in enumerate(ob_main.data.bones):
                            if p == b.parent:
                                a = j
                                break
                        if use_relbones == True:
                            p = matnorm(global_matrix @ ob_main.matrix_world @ b.parent.matrix_local)
                            m = p.inverted() @ m
                    # For the top level bones, we need model-space p,q
                    # for the children, parent relative p,q
                    p = m.to_translation()  # position
                    q = m.to_quaternion()  # orientation
                    q.normalize()
                    n = safestr(b.name)
                    try:
                        ni = strs[hash(str(n))][0]
                        name = "'" + b.name + "'"
                        if b.name != n:
                            name += " (" + n + ")"
                        report({"ERROR"}, "Bone name " + name + " not unique.")
                        use_skeleton = False
                        use_animation = False
                        bones = {}
                        break
                    except:
                        pass
                    bones[b.name] = [idx, [a, uniquedict(strs, n),
                                           uniquedict(verts, vert(
                                               round(p[0], digits),
                                               round(p[1], digits),
                                               round(p[2], digits), 1.0, 0, -1)),
                                           uniquedict(verts, vert(
                                               round(q.x, digits),
                                               round(q.y, digits),
                                               round(q.z, digits),
                                               round(q.w, digits), 0, -2))]]
                    idx = idx + 1
            if len(bones) < 1 and use_animation:
                report({"WARNING"}, "Skipping skeletal animation in lack of armature.")
                use_animation = False

        bpy.context.window_manager.progress_update(20)

        ### Mesh data ###
        depsgraph = context.evaluated_depsgraph_get()

        for i, ob_main in enumerate(objects):
            if ob_main.parent and ob_main.parent.instance_type in {'VERTS', 'FACES'}:
                continue

            obs = [(ob_main, ob_main.matrix_world)]
            if ob_main.is_instancer:
                obs += [(dup.instance_object.original, dup.matrix_world.copy())
                        for dup in depsgraph.object_instances
                        if dup.parent and dup.parent.original == ob_main]
            for ob, ob_mat in obs:
                if ob.type != 'MESH':
                    continue

                o = ob.evaluated_get(depsgraph) if use_mesh_modifiers else ob.original
                mesh = o.to_mesh()

                if use_name is None or use_name == '':
                    use_name = ob.name

                # Triangulate mesh (no effect if already triangulated)
                bm = bmesh.new()
                bm.from_mesh(mesh)
                bmesh.ops.triangulate(bm, faces=bm.faces[:], quad_method='BEAUTY', ngon_method='BEAUTY')
                bm.to_mesh(mesh)
                bm.free()

                # transform vertices to model-space
                mesh.transform(global_matrix @ ob_mat)
                if ob_mat.determinant() < 0.0:
                    mesh.flip_normals()
                if use_normals:
                    mesh.calc_normals_split()

                if use_skeleton and len(ob.vertex_groups) > 0:
                    vg = ob.vertex_groups
                else:
                    vg = []
                    if use_skeleton == True and use_animation == True:
                        report({"ERROR"},
                               "Mesh '" + mesh.name + "' in object '" + ob.name + "' has no vertex groups, no skeletal animation possible!")

                if use_uvs and len(mesh.uv_layers) > 0:
                    uv_layer = mesh.uv_layers.active.data[:]
                else:
                    uv_layer = []

                if use_colors and len(mesh.vertex_colors) > 0:
                    active_col_layer = mesh.vertex_colors.active
                    if active_col_layer is not None and len(active_col_layer.data) > 0:
                        vertex_colors = active_col_layer.data
                else:
                    vertex_colors = []

                matnames = []
                if use_materials:
                    for m in mesh.materials[:]:
                        if m and m.name:
                            matnames.append(uniquedict(strs, safestr(m.name)))
                        else:
                            matnames.append(-1)

                # vertices and faces
                badref = {}
                for pi, poly in enumerate(mesh.polygons):
                    face = [-1, [-1, -1, -1], [-1, -1, -1], [-1, -1, -1]]
                    if len(matnames) > 0:
                        if poly.material_index < len(matnames):
                            i = poly.material_index
                        else:
                            i = 0
                            # workaround to report each bad material index only once
                            try:
                                dummy = badref[poly.material_index]
                            except:
                                badref[poly.material_index] = 1
                                report({"ERROR"},
                                       "Polygon face in mesh '" + mesh.name + "' referencing a non-existent material (index " + str(
                                           poly.material_index) + ", largest can be " + str(len(matnames) - 1) + ").")
                        if i >= 0:
                            face[0] = matnames[i]
                            uniquedict(refmats, mesh.materials[i])
                    for i, li in enumerate(poly.loop_indices):
                        if len(vertex_colors) > 0:
                            c = uniquedict(cmap,
                                           [vertex_colors[li].color[0], vertex_colors[li].color[1],
                                            vertex_colors[li].color[2], vertex_colors[li].color[3]])
                        else:
                            c = 0
                        v = mesh.vertices[poly.vertices[i]]
                        if use_skeleton and len(vg) > 0 and len(v.groups) > 0:
                            wf = 0.0
                            for g in v.groups:
                                wf += g.weight
                            if wf > 0.0:
                                skin = []
                                w = wi = wm = 0
                                for g in v.groups:
                                    try:
                                        s = round(g.weight / wf * 255.0)
                                        if s > wm:
                                            wm = s
                                            si = len(skin)
                                        if s < 1:
                                            s = 1
                                        if s > 255:
                                            s = 255
                                        skin.append([bones[vg[g.group].name][0], s])
                                        w = w + s
                                    except:
                                        report({"ERROR"},
                                               "Vertex group name '" + vg[g.group].name + "' does not match any bone.")
                                        use_skeleton = False
                                        vg = []
                                        s = -1
                                        break
                                try:
                                    if w != 255:
                                        skin[si][1] += 255 - w
                                except:
                                    pass
                                s = uniquedict(skins, skin)
                                if len(skin) > nb_m:
                                    nb_m = len(skin)
                            else:
                                s = -1
                        else:
                            s = -1
                        face[1][i] = uniquedict(verts, vert(
                            round(v.co.x, digits),
                            round(v.co.y, digits),
                            round(v.co.z, digits), 1.0, c, s))
                        if use_normals:
                            try:
                                no = v.normal
                            except:
                                no = poly.loops[i].normal
                            no = no.normalized()  # also copies vector
                            face[3][i] = uniquedict(verts, vert(
                                round(no.x, digits),
                                round(no.y, digits),
                                round(no.z, digits), 1.0, 0, -1))
                            del no
                        if use_uvs and len(uv_layer) > 0:
                            face[2][i] = uniquedict(tmaps, list(uv_layer[li].uv[:]))
                    faces.append(face)
        faces.sort(key=itemgetter(0)) # group by material

        bpy.context.window_manager.progress_update(40)

        ### Materials ###
        if use_materials:
            for i, v in refmats.items():
                mi = v[0]
                mat = v[1]
                if mat is not None:
                    props = {}
                    if mat.node_tree:
                        # at least try to get the diffuse texture from other material types,
                        # because not all wrapped in PrincipledBSDF properly
                        for n in mat.node_tree.nodes:
                            if n.type == 'TEX_IMAGE' and n.image and n.image.name != "":
                                data = get_texturedata(n.image, use_inline)

                                s = uniquedict(strs, n.image.name)
                                if use_inline and len(data) > 8:
                                    uniquedict(inlined, [s, data])
                                props[128] = [128, s]
                                break
                    # otherwise properly parse material if blender can convert it into PrincipledBSDF
                    mat_wrap = node_shader_utils.PrincipledBSDFWrapper(mat)
                    if mat_wrap:
                        for key, mat_wrap_key in mat_property_map.items():
                            if key == 0:
                                # Kd
                                if mat_wrap.alpha != 0.0 and mat_wrap.alpha != 1.0:
                                    d = mat_wrap.alpha
                                elif mat_wrap.base_color and len(mat_wrap.base_color) > 3:
                                    d = mat_wrap.base_color[3]
                                else:
                                    d = 0.0
                                if d != 0.0:
                                    props[0] = [0, uniquedict(cmap, [mat_wrap.base_color[0], mat_wrap.base_color[1],
                                                                     mat_wrap.base_color[2], d])]
                            elif key == 8:
                                # il
                                il = 0
                                if mat_wrap.specular == 0:
                                    il = 1
                                elif mat_wrap.metallic != 0.0:
                                    if d != 1.0:
                                        il = 6
                                    else:
                                        il = 3
                                elif d != 1.0:
                                    il = 9
                                else:
                                    il = 2
                                if il != 0:
                                    props[8] = [8, il]
                            elif mat_wrap_key[0][0:2] == "//":
                                continue

                            try:
                                val = getattr(mat_wrap, mat_wrap_key[1], None)
                            except:
                                continue
                            if val is None:
                                continue

                            if key >= 128:
                                # according to the doc, texture material attributes should always have val.image
                                # but sometimes they don't...
                                if val.image is None or val.image.name == "":
                                    continue
                                data = get_texturedata(val.image, use_inline)
                                s = uniquedict(strs, val.image.name)
                                props[key] = [key, s]
                                if use_inline and len(data) > 8:
                                    uniquedict(inlined, [s, data])
                            elif mat_wrap_key[0] == "gscale" and val != 0.0:
                                props[key] = [key, uniquedict(cmap, [val, val, val, 1.0])]
                            elif mat_wrap_key[0] == "color" and len(val) == 3:
                                props[key] = [key, uniquedict(cmap, [val[0], val[1], val[2], 1.0])]
                            elif mat_wrap_key[0] == "color" and len(val) == 4:
                                props[key] = [key, uniquedict(cmap, val)]
                            elif mat_wrap_key[0] == "float" and val != 0.0:
                                props[key] = [key, val]
                            elif (mat_wrap_key[0] == "byte" or mat_wrap_key[0] == "int") and val != 0:
                                props[key] = [key, val]
                    else:
                        report({"ERROR"},
                               "Material '" + mat.name + "' does not use PrincipledBSDF surface, not parsing.")
                    # append material if it has at least one property
                    if len(props) > 0:
                        materials.append([uniquedict(strs, safestr(mat.name)), props])

        bpy.context.window_manager.progress_update(60)

        ### Actions ###
        if use_animation:
            if use_skeleton and len(bones) > 0:
                mpf = 1000.0 / use_fps  # msec per frame
                acts = []
                nf = 0  # number of total frames

                # collect actions from timeline markers, otherwise use actions
                if use_markers == True and len(scene.timeline_markers) > 0:
                    tlm = sorted(scene.timeline_markers, key=lambda tl: tl.frame)
                    for i, t in enumerate(tlm):
                        if i + 1 >= len(tlm):
                            et = scene.frame_end
                        else:
                            et = tlm[i + 1].frame - 1
                        if et > t.frame:
                            acts.append([safestr(t.name), -1, t.frame, et])
                            nf += et - t.frame
                else:
                    bpy_actions = bpy.data.actions
                    for i, a in enumerate(bpy_actions):
                        st = et = 0
                        if hasattr(a, 'curve_frame_range'):
                            frame_range = a.curve_frame_range
                        else:
                            frame_range = a.frame_range

                        st = int(frame_range[0])
                        et = int(frame_range[1])

                        if et > 0:
                            acts.append([safestr(a.name), i, st, et])
                        nf += et - st
                if nf == 0:
                    # no actions nor markers, one big happy animation only
                    acts.append(["Anim", -1, scene.frame_start, scene.frame_end])
                    nf = scene.frame_end - scene.frame_start
                # ok, now 'acts' is an array of [action name, action pose index, start frame, end frame]
                for a in acts:
                    # set action pose
                    scene.frame_set(0, subframe=0.0)
                    for i, ob_main in enumerate(objects):
                        if ob_main.type != "ARMATURE":
                            continue
                        if a[1] != -1:
                            ob_main.animation_data.action = bpy_actions[a[1]]
                        ob_main.data.pose_position = "POSE"
                        ob_main.data.update_tag()
                    lf = 0
                    frames = []  # collect frame with changed bones for this action
                    lastpose = {}  # fill up with bind pose on start
                    for n, b in bones.items():
                        lastpose[n] = [b[1][2], b[1][3]]
                    # iterate through each frame, and set anim pose for the armature
                    for frame in range(a[2], a[3] + 1):
                        scene.frame_set(frame, subframe=0.0)
                        # walk through the bones in anim pose, collect which one changed
                        changed = []
                        for i, ob_main in enumerate(objects):
                            if ob_main.type != "ARMATURE":
                                continue
                            for i, b in enumerate(ob_main.pose.bones):
                                try:
                                    idx = bones[b.name][0]
                                except:
                                    report({"ERROR"},
                                           "Animated bone name '" + b.name + "' does not match any bind-pose bone???")
                                    break
                                # we need model-space p,q only for bones without parents
                                m = matnorm(global_matrix @ ob_main.matrix_world @ b.matrix)
                                if use_relbones == True and b.parent:
                                    p = matnorm(global_matrix @ ob_main.matrix_world @ b.parent.matrix)
                                    m = p.inverted() @ m
                                p = m.to_translation()
                                q = m.to_quaternion()
                                q.normalize()
                                # differerent?
                                pos = uniquedict(verts, vert(
                                    round(p[0], digits),
                                    round(p[1], digits),
                                    round(p[2], digits), 1.0, 0, -1))
                                ori = uniquedict(verts, vert(
                                    round(q.x, digits),
                                    round(q.y, digits),
                                    round(q.z, digits),
                                    round(q.w, digits), 0, -2))
                                if lastpose[b.name][0] != pos or lastpose[b.name][1] != ori:
                                    changed.append([idx, pos, ori])
                                    lastpose[b.name][0] = pos
                                    lastpose[b.name][1] = ori
                        # do we have changed bones on this frame?
                        if len(changed) > 0:
                            if len(frames) < 1:
                                a[2] = frame
                            frames.append([int((frame - a[2]) * mpf), changed])
                            lf = frame
                            if len(changed) > fi_m:
                                fi_m = len(changed)
                    # if the action has at least one frame, save it
                    if len(frames) > 0:
                        actions.append([uniquedict(strs, safestr(a[0])), int((lf - a[2] + 1) * mpf), frames])
            else:
                report({"ERROR"}, "Trying to export animations without armature and skin")
        # restore original armature
        for i, ob_main in enumerate(objects):
            if ob_main.type == "ARMATURE":
                if oldaction is not None and ob_main.animation_data:
                    try:
                        ob_main.animation_data.action = oldaction
                    except:
                        continue
                ob_main.data.pose_position = oldpose[i]
                ob_main.data.update_tag()
        context.scene.frame_set(oldframe)

        bpy.context.window_manager.progress_update(75)

        # we need lists, but creating unique lists in python is impossible, so we
        # have used dictionaries. Let's convert those into lists now
        cmap = dict2list(cmap)
        strs = dict2list(strs)
        verts = dict2list(verts)
        tmaps = dict2list(tmaps)
        bones = dict2list(bones)
        skins = dict2list(skins)
        inlined = dict2list(inlined)
        # ----------------- End of Blender Specific Stuff ---------------------

        # Now we should have:
        #  cmap = array of [r, g, b, a]
        #  strs = array of unique strings
        #  verts = array of [x, y, z, w, color, skinid]
        #  tmaps = array of [u, v]
        #  faces = array of [material strid, [3] vertexids, [3] normalvertexids, [3] tmapids }
        #  shapes =
        #  labels =
        #  materials = array of [material strid, dict of [property type, property value]]
        #  bones = array of [parent, name strid, pos vertexid, ori vertexid]
        #  skins = array of [[boneid, weight] * 8]
        #  actions = array of [action name strid, durationmsec, array of animation frames]
        #    anim frame = [timestampmsec, array of [boneid, pos vertexid, ori vertexid]]
        #  inlined = array of [name strid, bytes data]
        #  extras = array of [bytes[4] magic, bytes data]

        # print("----------------------------------------------")
        # print(cmap)
        # print(strs)
        # print(verts)
        # print(tmaps)
        # print(faces)
        # print(shapes)
        # print(labels)
        # print(materials)
        # print(bones)
        # print(skins)
        # print(actions)
        # print(inlined)
        # print(extras)
        # print("----------------------------------------------")

        # normalize coordinates
        if use_gridcompress == True:
            min_x = min_y = min_z = 1e10
            max_x = max_y = max_z = -1e10
            for v in verts:
                if v[0] < min_x:
                    min_x = v[0]
                if v[0] > max_x:
                    max_x = v[0]
                if v[1] < min_y:
                    min_y = v[1]
                if v[1] > max_y:
                    max_y = v[1]
                if v[2] < min_z:
                    min_z = v[2]
                if v[2] > max_z:
                    max_z = v[2]
            s = max(abs(min_x), abs(max_x), abs(min_y), abs(max_y), abs(min_z), abs(max_z))
            if s != 1.0 and s != 0.0:
                for i, v in enumerate(verts):
                    if verts[i][5] != -2:
                        verts[i][0] = round(verts[i][0] / s, digits)
                        verts[i][1] = round(verts[i][1] / s, digits)
                        verts[i][2] = round(verts[i][2] / s, digits)
            if use_scale <= 0.0:
                use_scale = s
        if use_scale <= 0.0:
            use_scale = 1.0

        # Construct chunks buffer from lists
        print(len(verts), "verts,", len(faces), "faces,", len(tmaps), "UVs", len(materials), "materials,", len(bones),
              "bones,", len(skins), "skins,", len(actions), "actions")

        # create string table and calculate string offsets
        if use_author is None or use_author == "":
            use_author = os.getenv("LOGNAME", "")

        if use_ascii == True:
            # save Model 3D ASCII variant
            s = "3dmodel " + str(use_scale) + "\r\n"
            s += safestr(use_name, 2) + "\r\n"
            s += safestr(use_license, 2) + "\r\n"
            s += safestr(use_author, 2) + "\r\n"
            s += safestr(use_comment, 1) + "\r\n\r\n"

            # materials
            if len(materials) > 0:
                for m in materials:
                    s += "Material " + strs[m[0]] + "\r\n"
                    for pi, p in m[1].items():
                        t = mat_property_map[p[0]]
                        s += t[2] + " "
                        if t[0] == "color" or t[0] == "gscale":
                            s += "#"
                            for i in range(0, 4):
                                s += "%02x" % (int(cmap[p[1]][3 - i] * 255.0))
                        elif t[0] == "float":
                            s += str(round(p[1], digits))
                        elif p[0] >= 128:
                            s += strs[p[1]]
                        else:
                            s += str(p[1])
                        s += "\r\n"
                    s += "\r\n"

            # texture map
            if len(tmaps) > 0:
                s += "Textmap\r\n"
                r = True
                for t in tmaps:
                    # failsafes
                    if (t[0] < 0.0 or t[0] > 1.0 or t[1] < 0.0 or t[1] > 1.0) and not allow_unnormalized_uvs:
                        if r:
                            r = False
                            report({"ERROR"}, "Texture UV's are out of 0..1 range")
                        if t[0] > 1.0:
                            t[0] = 1.0
                        if t[0] < 0.0:
                            t[0] = 0.0
                        if t[1] > 1.0:
                            t[1] = 1.0
                        if t[1] < 0.0:
                            t[1] = 0.0
                    s += str(round(t[0], digits)) + " " + str(round(t[1], digits)) + "\r\n"
                s += "\r\n"

            # vertex list
            if len(verts) > 0:
                s += "Vertex\r\n"
                for v in verts:
                    s += str(v[0]) + " " + str(v[1]) + " " + str(v[2]) + " " + str(v[3])
                    if 0 <= v[4] < len(cmap):
                        s += " #"
                        for i in range(0, 4):
                            s += "%02x" % (int(cmap[v[4]][3 - i] * 255.0))
                    elif 0 <= v[5] < len(skins):
                        s += " #ffffffff"
                    if 0 <= v[5] < len(skins):
                        for i in range(0, min(len(skins[v[5]]), 8)):
                            if skins[v[5]][i][0] != -1 and skins[v[5]][i][1] != 0:
                                s += " " + str(skins[v[5]][i][0]) + ":" + str(
                                    round(float(skins[v[5]][i][1]) / 255.0, 4))
                    s += "\r\n"
                s += "\r\n"

            # triangle mesh
            if len(faces) > 0:
                s += "Mesh\r\n"
                l = -1
                for f in faces:
                    if l != f[0]:
                        l = f[0]
                        if l == -1:
                            s += "use\r\n"
                        else:
                            s += "use " + strs[l] + "\r\n"
                    for i, v in enumerate(f[1]):
                        if i != 0:
                            s += " "
                        s += str(v) + "/"
                        if use_uvs:
                            s += str(f[2][i])
                        s += "/"
                        if use_normals:
                            s += str(f[3][i])
                    s += "\r\n"
                s += "\r\n"

            # skeleton
            if len(bones) > 0 or len(skins) > 0:
                s += "Bones\r\n"
                s += bonestr(strs, bones, -1, 0)
                s += "\r\n"

            # actions (animations)
            if len(actions) > 0:
                for a in actions:
                    if len(a[2]) < 1:
                        continue
                    s += "Action " + str(a[1]) + " " + strs[a[0]] + "\r\n"
                    for f in a[2]:
                        s += "frame " + str(f[0]) + "\r\n"
                        for t in f[1]:
                            s += str(t[0]) + " " + str(t[1]) + " " + str(t[2]) + "\r\n"
                    s += "\r\n"

            # inlined assets
            if len(inlined) > 0:
                s += "Assets\r\n"
                for i in inlined:
                    s += strs[i[0]] + ".png\r\n"
                s += "\r\n"

            # write out file
            filepath = filepath[:len(filepath) - 4] + ".a3d"
            if use_strmcompress:
                import gzip
                # could have use gzip.open, but we need the compressed size too
                s = gzip.compress(bytes(s, 'utf-8'), 9)
                filepath += ".gz"
                f = open(filepath, 'wb')
            else:
                f = open(filepath, 'w')
            f.write(s)
            f.close()
            s = len(s)
        else:
            # save Model 3D binary variant
            stridx = [0] * (len(strs))
            st = bytes(safestr(use_name, 2), 'utf-8') + pack("<b", 0)
            st = st + bytes(safestr(use_license, 2), 'utf-8') + pack("<b", 0)
            st = st + bytes(safestr(use_author, 2), 'utf-8') + pack("<b", 0)
            st = st + bytes(safestr(use_comment, 1), 'utf-8') + pack("<b", 0)
            o = len(st)
            for i, s in enumerate(strs):
                s = bytes(s, 'utf-8') + pack("<b", 0)
                st = st + s
                stridx[i] = o
                o = o + len(s)

            # construct model header chunk
            ci_s = idxsize(len(cmap))
            ti_s = idxsize(len(tmaps))
            vi_s = idxsize(len(verts))
            si_s = idxsize(o)
            bi_s = idxsize(len(bones))
            sk_s = idxsize(len(skins))
            fi_s = idxsize(len(faces))
            if nb_m < 2:
                nb_s = 0
            elif nb_m == 2:
                nb_s = 1
            elif nb_m <= 4:
                nb_s = 2
            else:
                nb_s = 3
            fc_s = idxsize(fi_m)
            flags = (use_quality << 0) | (vi_s << 2) | (si_s << 4) | (ci_s << 6) | (ti_s << 8) | (bi_s << 10) | (
                    nb_s << 12)
            flags |= (sk_s << 14) | (fc_s << 16) | (fi_s << 20)
            buf = pack("<f", use_scale) + pack("<I", flags) + st
            buf = b'HEAD' + pack("<I", len(buf) + 8) + buf

            # color map
            if len(cmap) > 0 and ci_s < 4:
                byte_list = []
                for col in cmap:
                    for i in range(4):
                        byte_list.append(pack("<B", int(col[i] * 255)))
                buf = buf + b'CMAP' + pack("<I", len(cmap) * 4 + 8) + b''.join(byte_list)

            # texture map
            if len(tmaps) > 0:
                byte_list = []
                r = True
                for t in tmaps:
                    # failsafes
                    if (t[0] < 0.0 or t[0] > 1.0 or t[1] < 0.0 or t[1] > 1.0) and not allow_unnormalized_uvs:
                        if r:
                            r = False
                            report({"ERROR"}, "Texture UV's are out of 0..1 range")
                        t = [max(min(t[0], 1.0), 0.0), max(min(t[1], 1.0), 0.0)]
                    if use_quality == 0:
                        byte_list.append(pack("<BB", int(t[0] * 255), int(t[1] * 255)))
                    elif use_quality == 1:
                        byte_list.append(pack("<HH", int(t[0] * 65535), int(t[1] * 65535)))
                    elif use_quality == 3:
                        byte_list.append(pack("<dd", t[0], t[1]))
                    else:
                        byte_list.append(pack("<ff", t[0], t[1]))

                buf = buf + b'TMAP' + pack("<I", len(tmaps) * 2 * (1 << use_quality) + 8) + b''.join(byte_list)

            # vertex list
            if len(verts) > 0:
                byte_list = []
                for v in verts:
                    if use_quality == 0:
                        for i in range(4):
                            byte_list.append(pack("<b", int(v[i] * 127)))
                    elif use_quality == 1:
                        for i in range(4):
                            byte_list.append(pack("<h", int(v[i] * 32767)))
                    elif use_quality == 3:
                        for i in range(4):
                            byte_list.append(pack("<d", v[i]))
                    else:
                        for i in range(4):
                            byte_list.append(pack("<f", v[i]))

                    if ci_s < 4:
                        byte_list.append(addidx(ci_s, v[4]))
                    else:
                        byte_list.append(pack("<I", cmap[v[4]]))
                    byte_list.append(addidx(sk_s, v[5]))

                o = b''.join(byte_list)
                buf = buf + b'VRTS' + pack("<I", len(o) + 8) + o

            # skeleton
            if len(bones) > 0 or len(skins) > 0:
                byte_list = [addidx(bi_s, len(bones)), addidx(sk_s, len(skins))]
                for b in bones:
                    byte_list.extend(
                        [addidx(bi_s, b[0]), addidx(si_s, stridx[b[1]]), addidx(vi_s, b[2]), addidx(vi_s, b[3])])
                for s in skins:
                    if nb_s > 0:
                        for i in range(1 << nb_s):
                            if i >= len(s):
                                byte_list.append(pack("<B", 0))
                            else:
                                byte_list.append(pack("<B", s[i][1]))
                    for i in range(min(len(s), 1 << nb_s)):
                        if s[i][1] != 0:
                            byte_list.append(addidx(bi_s, s[i][0]))
                o = b''.join(byte_list)
                buf = buf + b'BONE' + pack("<I", len(o) + 8) + o

            # materials
            if len(materials) > 0:
                for m in materials:
                    byte_list = [addidx(si_s, stridx[m[0]])]
                    for pi, p in m[1].items():
                        byte_list.append(pack("<B", p[0]))
                        t = mat_property_map[p[0]]
                        if t[0] == "color" or t[0] == "gscale":
                            if ci_s < 4:
                                byte_list.append(addidx(ci_s, p[1]))
                            else:
                                byte_list.append(pack("<I", cmap[p[1]]))
                        elif t[0] == "byte" or t[0] == "//byte":
                            byte_list.append(pack("<B", p[1]))
                        elif p[0] >= 128:
                            byte_list.append(addidx(si_s, stridx[p[1]]))
                        else:
                            byte_list.append(pack("<f", p[1]))
                    o = b''.join(byte_list)
                    buf = buf + b'MTRL' + pack("<I", len(o) + 8) + o

            # triangle mesh
            if len(faces) > 0:
                l = -1
                byte_list = []
                for f in faces:
                    if l != f[0]:
                        l = f[0]
                        byte_list.append(pack("<b", 0))
                        byte_list.append(addidx(si_s, stridx[l]))
                    byte_list.append(pack("<b", (len(f[1]) << 4) | use_uvs | (use_normals << 1)))

                    if use_uvs and use_normals:
                        for i, v in enumerate(f[1]):
                            byte_list.append(addidx(vi_s, v))
                            byte_list.append(addidx(ti_s, f[2][i]))
                            byte_list.append(addidx(vi_s, f[3][i]))
                    elif use_uvs:
                        for i, v in enumerate(f[1]):
                            byte_list.append(addidx(vi_s, v))
                            byte_list.append(addidx(ti_s, f[2][i]))
                    elif use_normals:
                        for i, v in enumerate(f[1]):
                            byte_list.append(addidx(vi_s, v))
                            byte_list.append(addidx(vi_s, f[3][i]))
                    else:
                        for v in f[1]:
                            byte_list.append(addidx(vi_s, v))

                o = b''.join(byte_list)
                buf = buf + b'MESH' + pack("<I", len(o) + 8) + o

            # labels (usused for now)
            if len(labels) > 0:
                l = -1
                o = b''
                for f in labels:
                    o = o + b''
                buf = buf + b'LBLS' + pack("<I", len(o) + 8) + o

            # actions (animations)
            if len(actions) > 0:
                for a in actions:
                    if len(a[2]) < 1:
                        continue
                    byte_list = [addidx(si_s, stridx[a[0]]), pack("<H", len(a[2])), pack("<I", a[1])]
                    for f in a[2]:
                        byte_list.extend([pack("<I", f[0]), addidx(fc_s, len(f[1]))])
                        for t in f[1]:
                            byte_list.extend([addidx(bi_s, t[0]), addidx(vi_s, t[1]), addidx(vi_s, t[2])])
                    o = b''.join(byte_list)
                    buf = buf + b'ACTN' + pack("<I", len(o) + 8) + o

            # inlined assets
            if len(inlined) > 0:
                byte_list = [buf]
                for i in inlined:
                    o = addidx(si_s, stridx[i[0]]) + i[1]
                    byte_list.append(b'ASET' + pack("<I", len(o) + 8) + o)
                buf = b''.join(byte_list)

            # extra chunks
            if len(extras) > 0:
                byte_list = [buf]
                for e in extras:
                    byte_list.append(e[0][0:4] + pack("<I", len(e[1]) + 8) + e[1])
                buf = b''.join(byte_list)

            # End chunk
            buf = buf + b'OMD3'
            if use_strmcompress:
                buf = zlib.compress(buf, 9)

            # add file header and write out file
            with open(filepath, 'wb') as f:
                s = len(buf) + 8
                f.write(b'3DMO' + pack("<L", s) + buf)

        bpy.context.window_manager.progress_end()

        execution_time = "%.4f sec" % (time.time() - time_start)
        report({"INFO"}, "Model 3D export time taken: " + execution_time)

        report({"INFO"}, "Model 3D " + filepath + " (" + str(s) + " bytes) exported")
    return {'FINISHED'}


# -----------------------------------------------------------------------------
# Blender integration
import bpy
from bpy.props import (
    BoolProperty,
    FloatProperty,
    StringProperty,
    IntProperty,
    EnumProperty,
)
from bpy_extras.io_utils import (
    ExportHelper,
    ImportHelper,
    axis_conversion,
)


class ImportM3D(bpy.types.Operator, ImportHelper):
    """Load a Model 3D File (.m3d)"""

    bl_idname = "import_scene.m3d"
    bl_label = 'Import M3D'
    bl_options = {'PRESET'}

    filename_ext = ".m3d"
    filter_glob: StringProperty(
        default="*.m3d;*.a3d;*.a3d.gz",
        options={'HIDDEN'},
    )

    def execute(self, context):
        return read_m3d(context, self.filepath, self.report)


class ExportM3D(bpy.types.Operator, ExportHelper):
    """Save a Model 3D File (.m3d)"""

    bl_idname = "export_scene.m3d"
    bl_label = 'Export M3D'
    bl_options = {'PRESET'}

    filename_ext = ".m3d"
    filter_glob: StringProperty(
        default="*.m3d",
        options={'HIDDEN'},
    )

    # model properties
    use_name: StringProperty(
        name="Model Name",
        description="Name of the exported model",
        default="",
    )
    use_license: StringProperty(
        name="License",
        description="Licensing, copyright notice",
        default="MIT",
    )
    use_author: StringProperty(
        name="Author",
        description="Your name and contact (email, git repo url etc.)",
        default="",
    )
    use_comment: StringProperty(
        name="Comment",
        description="Any description or comment on the model",
        default="",
    )
    use_scale: FloatProperty(
        name="Scale (meter)",
        description="Specify model space 1.0 in SI meters (use 0.0 to calculate)",
        min=0.0, max=1000.0,
        default=1.0,
    )
    # import range
    use_selection: BoolProperty(
        name="Selection Only",
        description="Export selected objects only",
        default=False,
    )
    use_mesh_modifiers: BoolProperty(
        name="Apply Modifiers",
        description="Apply modifiers",
        default=True,
    )
    # export properties
    use_normals: BoolProperty(
        name="Include Normals",
        description="Export one normal per vertex and per face, to represent flat faces and sharp edges",
        default=True,
    )
    use_uvs: BoolProperty(
        name="Include UVs",
        description="Write out the active UV coordinates",
        default=True,
    )

    def update_unnormalized_uvs(self, context):
        if self.allow_unnormalized_uvs:
            self.use_quality = '2'
        else:
            self.use_quality = '-1'

    allow_unnormalized_uvs: BoolProperty(
        name="Allow Unnormalized UVs",
        description="Allow UV coordinates outside of the 0-1 range",
        update=update_unnormalized_uvs,
        default=False,
    )
    use_colors: BoolProperty(
        name="Include Vertex Colors",
        description="Write out individual vertex colors (independent to material colors)",
        default=True,
    )
    use_materials: BoolProperty(
        name="Write Materials",
        description="Write out the materials",
        default=True,
    )
    use_skeleton: BoolProperty(
        name="Write Armature",
        description="Write out armature (bones hiearachy and skin)",
        default=True,
    )
    use_animation: BoolProperty(
        name="Write Animation",
        description="Write out actions (implies armature)",
        default=True,
    )
    use_markers: BoolProperty(
        name="Use Markers",
        description="Use timeline markers for animations instead of actions",
        default=False,
    )
    use_fps: IntProperty(
        name="FPS",
        description="Specify frame per second. Blender only nows about frames",
        min=1, max=120,
        default=25,
    )

    def get_quality_items(self, context):
        if self.allow_unnormalized_uvs:
            # Only float and double precision options available
            items = [('2', '32 bits (float)', 'float precision coordinates (used by most other binary formats)'),
                     ('3', '64 bits (double)', 'double precision coordinates (rarely needed)')]
        else:
            # All options available
            items = [('-1', 'auto', 'choose depending on the number of polygons'),
                     ('0', '8 bits (int8)', '1/256 coordinate unit (for low poly models)'),
                     ('1', '16 bits (int16)', '1/65536 coordinate unit (more than enough in most cases)'),
                     ('2', '32 bits (float)', 'float precision coordinates (used by most other binary formats)'),
                     ('3', '64 bits (double)', 'double precision coordinates (rarely needed)')]

        return items

    use_quality: EnumProperty(
        name="Precision",
        items=get_quality_items,
        description="Coordinate grid system's size and precision",
    )
    use_inline: BoolProperty(
        name="Embed Assets",
        description="Inline assets (like textures) into output, create a single file that contains everything",
        default=False,
    )
    use_gridcompress: BoolProperty(
        name="Use Gridcompression",
        description="Use lossy compression, achieve much smaller files by sacrificing a little bit of model quality",
        default=True,
    )
    use_strmcompress: BoolProperty(
        name="Use Streamcompression",
        description="Use lossless deflate on binary data. Unless you're writing your own M3D parser, keep it checked",
        default=True,
    )
    use_ascii: BoolProperty(
        name="Use ASCII variant",
        description="Use plain text variant of Model 3D for output",
        default=False,
    )

    def execute(self, context):
        # Exit edit mode before exporting, so current object states are exported properly.
        if bpy.ops.object.mode_set.poll():
            bpy.ops.object.mode_set(mode='OBJECT')

        keywords = self.as_keywords(ignore=("filepath", "filter_glob"))
        return write_m3d(context, self.filepath, self.report, **keywords)


def menu_func_export(self, context):
    self.layout.operator(ExportM3D.bl_idname, text="Model 3D (.m3d)")


def menu_func_import(self, context):
    self.layout.operator(ImportM3D.bl_idname, text="Model 3D (.m3d/.a3d)")


def register():
    bpy.utils.register_class(ExportM3D)
    bpy.utils.register_class(ImportM3D)
    bpy.types.TOPBAR_MT_file_export.append(menu_func_export)

    # bpy.types.TOPBAR_MT_file_import.append(menu_func_import)


def unregister():
    bpy.types.TOPBAR_MT_file_export.remove(menu_func_export)
    # bpy.types.TOPBAR_MT_file_import.remove(menu_func_import)
    bpy.utils.unregister_class(ExportM3D)
    bpy.utils.unregister_class(ImportM3D)


if __name__ == "__main__":
    register()