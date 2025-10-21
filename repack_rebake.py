import bpy
import bmesh
import math
import os
from mathutils import Vector

# ==========================
# ===== Helper Utils =======
# ==========================

def log(msg):
    print("[AtlasRepacker]", msg)

def tri_area_uv(a, b, c):
    return abs((b.x - a.x)*(c.y - a.y) - (c.x - a.x)*(b.y - a.y)) * 0.5


def clamp01(v):
    return Vector((v.x - math.floor(v.x), v.y - math.floor(v.y)))


def active_uv_name(obj):
    uv = obj.data.uv_layers.active
    return uv.name if uv else None


def nearest_size(target, choices):
    return min(choices, key=lambda x: abs(x - target))


def compute_uv_area_fraction_object(obj, uv_name):
    """Estimate UV area fraction in [0..1] tile for the entire object."""
    me = obj.data
    bm = bmesh.new()
    bm.from_mesh(me)
    uv_layer = bm.loops.layers.uv.get(uv_name)
    if uv_layer is None:
        bm.free()
        return 0.0
    used = 0.0
    for f in bm.faces:
        if len(f.verts) < 3:
            continue
        uv0 = clamp01(Vector(f.loops[0][uv_layer].uv))
        for i in range(1, len(f.verts)-1):
            uvi = clamp01(Vector(f.loops[i][uv_layer].uv))
            uvj = clamp01(Vector(f.loops[i+1][uv_layer].uv))
            used += tri_area_uv(uv0, uvi, uvj)
    bm.free()
    return max(0.0, min(1.0, used))


def set_cycles_for_bake(samples=1):
    sc = bpy.context.scene
    sc.render.engine = 'CYCLES'
    sc.cycles.samples = samples
    sc.view_settings.view_transform = 'Standard'


def create_image(name, res, file_dir, colorspace='sRGB', alpha=True, fmt='PNG'):
    # If an image with this name already exists - remove it
    if name in bpy.data.images:
        old_img = bpy.data.images[name]
        bpy.data.images.remove(old_img)
    
    img = bpy.data.images.new(name, width=res, height=res, alpha=alpha, float_buffer=False)
    try:
        img.colorspace_settings.name = colorspace
    except Exception:
        pass
    if not file_dir:
        file_dir = bpy.path.abspath("//")
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in name)
    path = os.path.join(file_dir, f"{safe}.png" if fmt=='PNG' else f"{safe}.exr")
    img.filepath_raw = path
    img.file_format = fmt
    return img


def gather_material_images_and_roles(obj):
    """Scan all materials. Find basic roles: BaseColor, Normal, ORM/ARM and list of all TexImage nodes."""
    basecolor_nodes = []
    normal_nodes = []
    all_tex_nodes = []
    has_roughness = False
    has_metallic = False
    has_ao = False

    for mslot in obj.material_slots:
        mat = mslot.material
        if not mat or not mat.use_nodes:
            continue
        nt = mat.node_tree
        for n in nt.nodes:
            if n.type == 'TEX_IMAGE' and n.image:
                all_tex_nodes.append((mat, n))
        for n in nt.nodes:
            if n.type == 'BSDF_PRINCIPLED':
                if n.inputs.get('Base Color') and n.inputs['Base Color'].is_linked:
                    src = n.inputs['Base Color'].links[0].from_node
                    if src.type == 'TEX_IMAGE' and src.image:
                        basecolor_nodes.append((mat, src))
                if n.inputs.get('Normal') and n.inputs['Normal'].is_linked:
                    srcn = n.inputs['Normal'].links[0].from_node
                    if srcn.type == 'NORMAL_MAP':
                        if srcn.inputs.get('Color') and srcn.inputs['Color'].is_linked:
                            tn = srcn.inputs['Color'].links[0].from_node
                            if tn.type == 'TEX_IMAGE' and tn.image:
                                normal_nodes.append((mat, tn))
                    elif srcn.type == 'TEX_IMAGE' and srcn.image:
                        normal_nodes.append((mat, srcn))
                
                # Check for Roughness presence
                if n.inputs.get('Roughness') and n.inputs['Roughness'].is_linked:
                    has_roughness = True
                
                # Check for Metallic presence
                if n.inputs.get('Metallic') and n.inputs['Metallic'].is_linked:
                    has_metallic = True

    basecolor_image = basecolor_nodes[0][1].image if basecolor_nodes else None
    normal_image = normal_nodes[0][1].image if normal_nodes else None

    orm_candidates = []
    for mat, tex in all_tex_nodes:
        img = tex.image
        if img == basecolor_image or img == normal_image:
            continue
        lname = (img.name or "").lower()
        if any(k in lname for k in ("orm","arm","oem","occ","occlusion","rough","roughness","met","metal","metallic","ao")):
            orm_candidates.append(img)
            # If ORM texture is found, consider all channels present
            if "orm" in lname or "arm" in lname or "oem" in lname:
                has_roughness = True
                has_metallic = True
                has_ao = True
            elif "ao" in lname or "occ" in lname:
                has_ao = True
            elif "rough" in lname:
                has_roughness = True
            elif "met" in lname:
                has_metallic = True
                
    orm_image = orm_candidates[0] if orm_candidates else None

    return {
        "basecolor": basecolor_image,
        "normal": normal_image,
        "orm": orm_image,
        "has_roughness": has_roughness,
        "has_metallic": has_metallic,
        "has_ao": has_ao,
        "all_tex_nodes": all_tex_nodes
    }


def add_temp_node(nt, node_type, loc=(0,0)):
    n = nt.nodes.new(node_type)
    n.location = loc
    return n


def ensure_active_image_node_for_bake(mat, image):
    nt = mat.node_tree
    # Create single selected Image node receiver
    node = add_temp_node(nt, 'ShaderNodeTexImage', (-260, -160))
    node.image = image
    nt.nodes.active = node
    for n in nt.nodes:
        n.select = False
    node.select = True
    return node


def _op_has_prop(op_type, prop_name):
    try:
        return prop_name in bpy.types.UV_OT_pack_islands.bl_rna.properties
    except Exception:
        return False


def make_new_uv(obj, new_uv_name, pack_method='PACK', margin=0.002, rotate=False, average_scale=True):
    """Creates a new UV layer based on the source:
    PACK: copies UVs from source layer and applies Pack Islands (ShapeMethod=CONCAVE, no-rotate, MarginMethod=SCALED).
    RESCALE: scale+translate source islands to 0..1 without changing shape.
    """
    me = obj.data
    
    # Find source UV (not RepackRebake_UV)
    src = None
    for uv_layer in me.uv_layers:
        if uv_layer.name != new_uv_name:
            src = uv_layer
            break
    
    # If UV with this name already exists - remove it
    if new_uv_name in me.uv_layers:
        me.uv_layers.remove(me.uv_layers[new_uv_name])
    
    uv_new = me.uv_layers.new(name=new_uv_name)
    me.uv_layers.active = uv_new

    if pack_method == 'RESCALE' and src:
        bm = bmesh.new()
        bm.from_mesh(me)
        src_layer = bm.loops.layers.uv.get(src.name)
        dst_layer = bm.loops.layers.uv.get(uv_new.name)
        if src_layer and dst_layer:
            umin, vmin =  1e9,  1e9
            umax, vmax = -1e9, -1e9
            for f in bm.faces:
                for l in f.loops:
                    uv = clamp01(Vector(l[src_layer].uv))
                    umin, vmin = min(umin, uv.x), min(vmin, uv.y)
                    umax, vmax = max(umax, uv.x), max(vmax, uv.y)
            w = max(1e-6, umax-umin)
            h = max(1e-6, vmax-vmin)
            scale = 1.0 / max(w, h)
            s2 = (1.0 - margin*2.0)
            for f in bm.faces:
                for l in f.loops:
                    uv = clamp01(Vector(l[src_layer].uv))
                    uv.x = (uv.x - umin) * scale * s2 + margin
                    uv.y = (uv.y - vmin) * scale * s2 + margin
                    l[dst_layer].uv = uv
        bm.to_mesh(me); bm.free(); me.update()
    else:
        # PACK: copy source UV and apply pack islands (WITHOUT unwrap!)
        if src:
            bm = bmesh.new()
            bm.from_mesh(me)
            src_layer = bm.loops.layers.uv.get(src.name)
            dst_layer = bm.loops.layers.uv.get(uv_new.name)
            if src_layer and dst_layer:
                # Copy UV coordinates from source layer
                for f in bm.faces:
                    for l in f.loops:
                        l[dst_layer].uv = l[src_layer].uv
            bm.to_mesh(me)
            bm.free()
        
        # Update mesh
        me.update()
        
        # Ensure object is in OBJECT mode
        if obj.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        
        # Set new UV as active
        me.uv_layers.active = uv_new
        
        # Switch to EDIT mode
        bpy.ops.object.mode_set(mode='EDIT')
        
        # Select all faces and UVs
        bpy.ops.mesh.select_all(action='SELECT')
        bpy.ops.uv.select_all(action='SELECT')
        
        # Average islands scale if requested (ensures uniform texel density)
        if average_scale:
            try:
                bpy.ops.uv.average_islands_scale()
                log("Applied average_islands_scale")
            except Exception as e:
                log(f"average_islands_scale failed (may not be available): {e}")
        
        # In Blender 4.x pack_islands signature differs between versions.
        # Try calling operator with multiple argument sets (from newest to basic).
        called = False
        for kwargs in (
            {"rotate": False, "margin": margin, "shape_method": 'CONCAVE', "margin_method": 'SCALED', "udim_source": 'CLOSEST_UDIM', "scale": True},
            {"rotate": False, "margin": margin, "shape_method": 'CONCAVE', "margin_method": 'SCALED', "scale": True},
            {"rotate": False, "margin": margin, "shape_method": 'CONCAVE', "margin_method": 'SCALED'},
            {"rotate": False, "margin": margin},
        ):
            try:
                log(f"Calling pack_islands with: {kwargs}")
                result = bpy.ops.uv.pack_islands(**kwargs)
                log(f"pack_islands result: {result}")
                called = True
                break
            except TypeError as e:
                # Parameter missing in this version — try next set
                log(f"pack_islands signature not supported, retrying with fewer args: {e}")
                continue
            except Exception as e:
                log(f"pack_islands failed: {e}")
                import traceback
                log(traceback.format_exc())
                break
        if not called:
            log("pack_islands could not be called with any known signature")
        
        bpy.ops.object.mode_set(mode='OBJECT')

    # Return the name we set instead of reading uv_new.name (can trigger UnicodeDecodeError)
    return new_uv_name


def make_new_uv_multi_object(objects, new_uv_name, pack_method='PACK', margin=0.002, average_scale=True):
    """Creates UV layers for multiple objects and packs them together as one atlas.
    This function creates new UV layers for each object and then uses multi-object edit mode
    to pack all UV islands together as if they were one mesh.
    """
    if not objects:
        return
    
    # Step 1: Prepare UV layers for all objects
    for obj in objects:
        me = obj.data
        # Find source UV (not RepackRebake_UV)
        src = None
        for uv_layer in me.uv_layers:
            if uv_layer.name != new_uv_name:
                src = uv_layer
                break
        
        # Remove existing RepackRebake_UV if exists
        if new_uv_name in me.uv_layers:
            me.uv_layers.remove(me.uv_layers[new_uv_name])
        
        # Create new UV layer
        uv_new = me.uv_layers.new(name=new_uv_name)
        me.uv_layers.active = uv_new
        
        # Copy UV from source
        if pack_method == 'PACK' and src:
            bm = bmesh.new()
            bm.from_mesh(me)
            src_layer = bm.loops.layers.uv.get(src.name)
            dst_layer = bm.loops.layers.uv.get(new_uv_name)
            if src_layer and dst_layer:
                for f in bm.faces:
                    for l in f.loops:
                        l[dst_layer].uv = l[src_layer].uv
            bm.to_mesh(me)
            bm.free()
        me.update()
    
    # Step 2: Ensure all objects are in OBJECT mode and selected
    bpy.ops.object.mode_set(mode='OBJECT')
    bpy.ops.object.select_all(action='DESELECT')
    for obj in objects:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = objects[0]
    
    # Step 3: Enter multi-object EDIT mode
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_all(action='SELECT')
    bpy.ops.uv.select_all(action='SELECT')
    
    # Average islands scale if requested (ensures uniform texel density)
    if average_scale:
        try:
            bpy.ops.uv.average_islands_scale()
            log("Multi-object: Applied average_islands_scale")
        except Exception as e:
            log(f"Multi-object: average_islands_scale failed (may not be available): {e}")
    
    # Step 4: Pack islands - will pack all objects together as one atlas!
    called = False
    for kwargs in (
        {"rotate": False, "margin": margin, "shape_method": 'CONCAVE', "margin_method": 'SCALED', "udim_source": 'CLOSEST_UDIM', "scale": True},
        {"rotate": False, "margin": margin, "shape_method": 'CONCAVE', "margin_method": 'SCALED', "scale": True},
        {"rotate": False, "margin": margin, "shape_method": 'CONCAVE', "margin_method": 'SCALED'},
        {"rotate": False, "margin": margin},
    ):
        try:
            log(f"Multi-object pack_islands with: {kwargs}")
            result = bpy.ops.uv.pack_islands(**kwargs)
            log(f"Multi-object pack_islands result: {result}")
            called = True
            break
        except TypeError:
            continue
        except Exception as e:
            log(f"Multi-object pack_islands failed: {e}")
            import traceback
            log(traceback.format_exc())
            break
    
    if not called:
        log("Multi-object pack_islands could not be called with any known signature")
    
    # Step 5: Return to OBJECT mode
    bpy.ops.object.mode_set(mode='OBJECT')
    
    return new_uv_name


def build_simplified_material(obj_name, img_base, img_orm, img_norm):
    mat_name = f"Rebaked_{obj_name}"
    
    # If a material with this name already exists - remove it
    if mat_name in bpy.data.materials:
        old_mat = bpy.data.materials[mat_name]
        bpy.data.materials.remove(old_mat)
    
    mat = bpy.data.materials.new(mat_name)
    mat.use_nodes = True
    nt = mat.node_tree
    for n in list(nt.nodes):
        nt.nodes.remove(n)

    tex_base = None
    if img_base:
        tex_base = nt.nodes.new('ShaderNodeTexImage'); tex_base.image = img_base; tex_base.label = "BaseColor"; tex_base.location=(0, 180)

    tex_orm = None
    if img_orm:
        tex_orm = nt.nodes.new('ShaderNodeTexImage'); tex_orm.image = img_orm; tex_orm.label = "ORM(R=AO,G=Rough,B=Metal)"; tex_orm.location=(0, -40)
        try:
            tex_orm.image.colorspace_settings.name = 'Non-Color'
        except Exception:
            pass

    tex_norm = None
    if img_norm:
        tex_norm = nt.nodes.new('ShaderNodeTexImage'); tex_norm.image = img_norm; tex_norm.label = "Normal"; tex_norm.location=(0, -260)
        try:
            tex_norm.image.colorspace_settings.name = 'Non-Color'
        except Exception:
            pass

    bsdf = nt.nodes.new('ShaderNodeBsdfPrincipled'); bsdf.location = (360, 60)

    # Try to use the built-in "glTF Material Output" group if present
    gltf_out = None
    for ng in bpy.data.node_groups:
        if ng.name == 'glTF Material Output':
            gltf_out = ng; break
    if gltf_out:
        outp = nt.nodes.new('ShaderNodeGroup'); outp.node_tree = gltf_out; outp.location = (700, 40)
    else:
        outp = nt.nodes.new('ShaderNodeOutputMaterial'); outp.location = (700, 40)

    if tex_base:
        nt.links.new(tex_base.outputs['Color'], bsdf.inputs['Base Color'])

    if tex_orm:
        sep = nt.nodes.new('ShaderNodeSeparateRGB'); sep.location = (180, -40)
        nt.links.new(tex_orm.outputs['Color'], sep.inputs['Image'])
        # G -> Roughness, B -> Metallic (without multiplying AO into BaseColor)
        nt.links.new(sep.outputs['G'], bsdf.inputs['Roughness'])
        nt.links.new(sep.outputs['B'], bsdf.inputs['Metallic'])
        # R (AO) -> glTF Occlusion if the input exists
        if isinstance(outp, bpy.types.ShaderNodeGroup):
            if 'Occlusion' in outp.inputs:
                nt.links.new(sep.outputs['R'], outp.inputs['Occlusion'])

    if tex_norm:
        nrm = nt.nodes.new('ShaderNodeNormalMap'); nrm.location=(180, -260)
        nt.links.new(tex_norm.outputs['Color'], nrm.inputs['Color'])
        nt.links.new(nrm.outputs['Normal'], bsdf.inputs['Normal'])

    # Link BSDF -> Output/Group Surface
    if isinstance(outp, bpy.types.ShaderNodeGroup):
        if 'Surface' in outp.inputs:
            nt.links.new(bsdf.outputs['BSDF'], outp.inputs['Surface'])
    else:
        nt.links.new(bsdf.outputs['BSDF'], outp.inputs['Surface'])

    return mat


def rebake_single_object(obj, context, s, size_choices, save_dir, uv_name):
    """Helper function to bake maps for a single object (extracted from original execute method)"""
    # Find UV layer created during Repack step
    if uv_name not in obj.data.uv_layers:
        return None, f"{obj.name}: UV layer '{uv_name}' not found. Please run Repack UV first."

    # Find source UV (first non-RepackRebake_UV layer)
    src_uv = None
    for uv_layer in obj.data.uv_layers:
        if uv_layer.name != uv_name:
            src_uv = uv_layer.name
            break
    
    if not src_uv:
        return None, f"{obj.name}: no source UV — skipped."

    roles = gather_material_images_and_roles(obj)
    atlas_res = 1024
    if roles["basecolor"]:
        atlas_res = max(roles["basecolor"].size)
    else:
        for _, tex in roles["all_tex_nodes"]:
            atlas_res = max(atlas_res, max(tex.image.size))

    area_frac = compute_uv_area_fraction_object(obj, src_uv)
    target_linear = math.sqrt(max(1e-6, area_frac)) * atlas_res
    target_res = nearest_size(target_linear, size_choices)
    if s.ar_debug:
        log(f"{obj.name}: area_frac={area_frac:.4f} atlas_res={atlas_res} -> target_res={target_res}")

    # Check for input data before creating images
    img_base = None
    if s.ar_do_basecolor and roles["basecolor"]:
        img_base = create_image(f"Rebaked_{obj.name}_BaseColor_{target_res}", target_res, save_dir, colorspace='sRGB', alpha=True)
    
    img_orm = None
    if s.ar_do_orm and (roles["orm"] or roles["has_roughness"] or roles["has_metallic"] or roles["has_ao"]):
        img_orm = create_image(f"Rebaked_{obj.name}_ORM_{target_res}", target_res, save_dir, colorspace='Non-Color', alpha=False)
    
    img_norm = None
    if s.ar_do_normal and roles["normal"]:
        img_norm = create_image(f"Rebaked_{obj.name}_Normal_{target_res}", target_res, save_dir, colorspace='Non-Color', alpha=False)
    
    # If nothing to bake - skip object
    if not img_base and not img_orm and not img_norm:
        return None, f"{obj.name}: no data to bake — skipped."

    me = obj.data
    
    # Temporary material for baking
    tech_mat = bpy.data.materials.new(f"_TMP_Bake_{obj.name}")
    tech_mat.use_nodes = True
    # Replace all materials with temporary one
    me.materials.clear()
    me.materials.append(tech_mat)

    # Ensure active object
    for o in context.selected_objects:
        o.select_set(False)
    obj.select_set(True)
    context.view_layer.objects.active = obj

    # Write results to RepackRebake_UV
    obj.data.uv_layers.active = obj.data.uv_layers.get(uv_name)

    nt = tech_mat.node_tree

    # ===== BaseColor (EMIT) =====
    if img_base:
        nt.nodes.clear()
        src_img = roles["basecolor"] or (roles["all_tex_nodes"][0][1].image if roles["all_tex_nodes"] else None)
        if src_img:
            tex = nt.nodes.new('ShaderNodeTexImage'); tex.image = src_img; tex.location=(0,0)
            uvm = nt.nodes.new('ShaderNodeUVMap'); uvm.uv_map = src_uv; uvm.location=(-220,0)
            nt.links.new(uvm.outputs['UV'], tex.inputs['Vector'])
            emis = nt.nodes.new('ShaderNodeEmission'); emis.location=(200,0)
            outm = nt.nodes.new('ShaderNodeOutputMaterial'); outm.location=(420,0)
            nt.links.new(tex.outputs['Color'], emis.inputs['Color'])
            nt.links.new(emis.outputs['Emission'], outm.inputs['Surface'])

            ensure_active_image_node_for_bake(tech_mat, img_base)
            try:
                bpy.ops.object.bake(type='EMIT', margin=8)
                img_base.save()
                img_base.pack()
            except Exception as e:
                if s.ar_debug:
                    log(f"{obj.name}: BaseColor bake error: {e}")

    # ===== ORM (R=AO, G=Rough, B=Metal) =====
    if img_orm:
        nt.nodes.clear()
        orm_img = roles["orm"]
        if orm_img is None and roles["all_tex_nodes"]:
            for _, tex in roles["all_tex_nodes"]:
                name = (tex.image.name or "").lower()
                if any(k in name for k in ("orm","arm","oem","occ","occlusion","rough","roughness","met","metal","metallic","ao")):
                    orm_img = tex.image
                    break
        if orm_img:
            tex = nt.nodes.new('ShaderNodeTexImage'); tex.image = orm_img; tex.location=(0,0)
            try:
                tex.image.colorspace_settings.name = 'Non-Color'
            except Exception:
                pass
            uvm = nt.nodes.new('ShaderNodeUVMap'); uvm.uv_map = src_uv; uvm.location=(-220,0)
            nt.links.new(uvm.outputs['UV'], tex.inputs['Vector'])
            sep = nt.nodes.new('ShaderNodeSeparateRGB'); sep.location = (200, 0)
            nt.links.new(tex.outputs['Color'], sep.inputs['Image'])
            emis = nt.nodes.new('ShaderNodeEmission'); emis.location = (420, 0)
            outm = nt.nodes.new('ShaderNodeOutputMaterial'); outm.location=(640,0)
            nt.links.new(emis.outputs['Emission'], outm.inputs['Surface'])

            ensure_active_image_node_for_bake(tech_mat, img_orm)
            # R
            nt.links.new(sep.outputs['R'], emis.inputs['Color'])
            try:
                bpy.ops.object.bake(type='EMIT', margin=8)
            except Exception as e:
                if s.ar_debug:
                    log(f"{obj.name}: ORM R bake error: {e}")
            # G
            nt.links.new(sep.outputs['G'], emis.inputs['Color'])
            try:
                bpy.ops.object.bake(type='EMIT', margin=8)
            except Exception as e:
                if s.ar_debug:
                    log(f"{obj.name}: ORM G bake error: {e}")
            # B
            nt.links.new(sep.outputs['B'], emis.inputs['Color'])
            try:
                bpy.ops.object.bake(type='EMIT', margin=8)
            except Exception as e:
                if s.ar_debug:
                    log(f"{obj.name}: ORM B bake error: {e}")

            try:
                img_orm.save()
                img_orm.pack()
            except Exception:
                pass

    # ===== NORMAL =====
    if img_norm:
        nt.nodes.clear()
        nimg = roles["normal"]
        if nimg:
            tex = nt.nodes.new('ShaderNodeTexImage'); tex.image = nimg; tex.location=(0,0)
            try:
                tex.image.colorspace_settings.name = 'Non-Color'
            except Exception:
                pass
            uvm = nt.nodes.new('ShaderNodeUVMap'); uvm.uv_map = src_uv; uvm.location=(-220,0)
            nt.links.new(uvm.outputs['UV'], tex.inputs['Vector'])
            nrm = nt.nodes.new('ShaderNodeNormalMap'); nrm.location=(220,0)
            nt.links.new(tex.outputs['Color'], nrm.inputs['Color'])
            outm = nt.nodes.new('ShaderNodeOutputMaterial'); outm.location=(520,0)
            nt.links.new(nrm.outputs['Normal'], outm.inputs['Surface'])

            ensure_active_image_node_for_bake(tech_mat, img_norm)
            try:
                bpy.ops.object.bake(type='NORMAL', margin=8)
                img_norm.save()
                img_norm.pack()
            except Exception as e:
                if s.ar_debug:
                    log(f"{obj.name}: Normal bake error: {e}")

    # Final material with glTF-compatible links (replace old materials)
    final_mat = build_simplified_material(obj.name, img_base or None, img_orm or None, img_norm or None)
    me.materials.clear()
    me.materials.append(final_mat)

    # Clean up temporary material
    try:
        bpy.data.materials.remove(tech_mat)
    except Exception:
        pass
    
    # Set RepackRebake_UV active for viewport and render (at the end!)
    if uv_name in me.uv_layers:
        uv_layer = me.uv_layers[uv_name]
        # Set active UV for viewport
        me.uv_layers.active = uv_layer
        # Set active UV for render (Active Render)
        uv_layer.active_render = True
        # Also set active index
        for i, layer in enumerate(me.uv_layers):
            if layer.name == uv_name:
                me.uv_layers.active_index = i
                break
        # Update mesh
        me.update()
        if s.ar_debug:
            log(f"{obj.name}: UV '{uv_name}' set active (index={me.uv_layers.active_index}, name={me.uv_layers.active.name}, active_render={uv_layer.active_render})")

    return final_mat, None


def rebake_multi_object(objects, context, s, size_choices, save_dir, uv_name):
    """Bake multiple objects as one by temporarily joining them, then assign result to all originals"""
    log(f"Multi-object baking: processing {len(objects)} objects")
    
    # Step 1: Create duplicates of all objects
    bpy.ops.object.select_all(action='DESELECT')
    duplicates = []
    for obj in objects:
        # Duplicate object
        obj.select_set(True)
        context.view_layer.objects.active = obj
        bpy.ops.object.duplicate()
        dup = context.active_object
        duplicates.append(dup)
        obj.select_set(False)
    
    # Step 2: Join all duplicates into one temporary mesh
    bpy.ops.object.select_all(action='DESELECT')
    for dup in duplicates:
        dup.select_set(True)
    context.view_layer.objects.active = duplicates[0]
    bpy.ops.object.join()
    combined_obj = context.active_object
    combined_obj.name = "_TMP_Combined_Bake"
    
    log(f"Created combined mesh: {combined_obj.name}")
    
    # Step 3: Bake the combined mesh using single-object logic
    bpy.ops.object.select_all(action='DESELECT')
    combined_obj.select_set(True)
    context.view_layer.objects.active = combined_obj
    
    final_mat, error = rebake_single_object(combined_obj, context, s, size_choices, save_dir, uv_name)
    
    if error:
        # Cleanup and return error
        bpy.data.objects.remove(combined_obj, do_unlink=True)
        return None, error
    
    # Step 4: Assign the final material to all original objects
    for obj in objects:
        obj.data.materials.clear()
        obj.data.materials.append(final_mat)
        
        # Set RepackRebake_UV active for each object
        if uv_name in obj.data.uv_layers:
            uv_layer = obj.data.uv_layers[uv_name]
            obj.data.uv_layers.active = uv_layer
            uv_layer.active_render = True
            for i, layer in enumerate(obj.data.uv_layers):
                if layer.name == uv_name:
                    obj.data.uv_layers.active_index = i
                    break
            obj.data.update()
    
    # Step 5: Delete the temporary combined mesh
    bpy.data.objects.remove(combined_obj, do_unlink=True)
    log(f"Multi-object baking completed, temporary mesh removed")
    
    return final_mat, None


# ==========================
# ===== UI / Operator ======
# ==========================

class ATLASREPACK_PT_panel(bpy.types.Panel):
    bl_label = "Atlas Repacker"
    bl_idname = "ATLASREPACK_PT_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Atlas Repacker"

    def draw(self, ctx):
        s = ctx.scene
        col = self.layout.column(align=True)
        col.label(text="Multi-Object Mode")
        col.prop(s, "ar_multi_object")
        col.separator()
        col.label(text="UV Settings")
        col.prop(s, "ar_uv_margin")
        col.prop(s, "ar_average_scale")
        col.prop(s, "ar_pack_method", text="UV method")
        col.separator()
        col.operator("atlas_repacker.repack_uv", icon='UV')
        col.separator()
        col.label(text="Texture Sizes")
        col.prop(s, "ar_size_choices")
        col.prop(s, "ar_min_size")
        col.separator()
        col.label(text="Maps to bake")
        col.prop(s, "ar_do_basecolor")
        col.prop(s, "ar_do_orm")
        col.prop(s, "ar_do_normal")
        col.separator()
        col.operator("atlas_repacker.rebake_maps", icon='RENDER_STILL')
        col.separator()
        col.prop(s, "ar_debug")


class ATLASREPACK_OT_repack_uv(bpy.types.Operator):
    bl_idname = "atlas_repacker.repack_uv"
    bl_label = "Repack UV"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        s = context.scene
        sel = [o for o in context.selected_objects if o.type=='MESH']
        if not sel:
            self.report({'WARNING'}, "Select at least one MESH object.")
            return {'CANCELLED'}

        uv_name = "RepackRebake_UV"
        
        # Check if multi-object mode is enabled
        if s.ar_multi_object and len(sel) > 1:
            # Multi-object mode: pack UV islands together as one atlas
            log(f"Multi-object mode: packing {len(sel)} objects together")
            
            # Check that all objects have source UV
            valid_objects = []
            for obj in sel:
                has_source_uv = False
                for uv_layer in obj.data.uv_layers:
                    if uv_layer.name != uv_name:
                        has_source_uv = True
                        break
                
                if has_source_uv:
                    valid_objects.append(obj)
                else:
                    self.report({'INFO'}, f"{obj.name}: no source UV — skipped.")
            
            if not valid_objects:
                self.report({'WARNING'}, "No objects with source UV found.")
                return {'CANCELLED'}
            
            # Pack all objects together
            make_new_uv_multi_object(
                valid_objects, uv_name,
                pack_method=s.ar_pack_method,
                margin=s.ar_uv_margin,
                average_scale=s.ar_average_scale
            )
            
            self.report({'INFO'}, f"UV Repack completed for {len(valid_objects)} object(s) as single atlas.")
        else:
            # Single-object mode: pack each object separately (original behavior)
            for obj in sel:
                if obj.mode != 'OBJECT':
                    bpy.ops.object.mode_set(mode='OBJECT')

                # Check that a source UV exists
                has_source_uv = False
                for uv_layer in obj.data.uv_layers:
                    if uv_layer.name != uv_name:
                        has_source_uv = True
                        break
                
                if not has_source_uv:
                    self.report({'INFO'}, f"{obj.name}: no source UV — skipped.")
                    continue

                new_uv = make_new_uv(
                    obj, uv_name,
                    pack_method=s.ar_pack_method,
                    margin=s.ar_uv_margin,
                    rotate=False,
                    average_scale=s.ar_average_scale
                )
                if s.ar_debug:
                    log(f"{obj.name}: UV layer '{new_uv}' updated")

            self.report({'INFO'}, f"UV Repack completed for {len(sel)} object(s).")
        
        return {'FINISHED'}


class ATLASREPACK_OT_rebake_maps(bpy.types.Operator):
    bl_idname = "atlas_repacker.rebake_maps"
    bl_label = "Rebake Maps"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        s = context.scene
        try:
            size_choices = [int(x) for x in s.ar_size_choices.split(",")]
            size_choices = sorted(set([max(s.ar_min_size, v) for v in size_choices]))
        except Exception:
            size_choices = [128,256,512,1024,2048,4096]
        save_dir = bpy.path.abspath("//")

        set_cycles_for_bake(samples=1)

        sel = [o for o in context.selected_objects if o.type=='MESH']
        if not sel:
            self.report({'WARNING'}, "Select at least one MESH object.")
            return {'CANCELLED'}

        uv_name = "RepackRebake_UV"
        
        # Check if multi-object mode is enabled
        if s.ar_multi_object and len(sel) > 1:
            # Multi-object mode: bake all objects as one
            log(f"Multi-object baking mode: processing {len(sel)} objects as one")
            
            # Check that all objects have RepackRebake_UV
            valid_objects = []
            for obj in sel:
                if obj.mode != 'OBJECT':
                    bpy.ops.object.mode_set(mode='OBJECT')
                if uv_name in obj.data.uv_layers:
                    valid_objects.append(obj)
                else:
                    self.report({'WARNING'}, f"{obj.name}: UV layer '{uv_name}' not found. Skipped.")
            
            if not valid_objects:
                self.report({'WARNING'}, "No objects with RepackRebake_UV found. Please run Repack UV first.")
                return {'CANCELLED'}
            
            # Bake all objects as one
            final_mat, error = rebake_multi_object(valid_objects, context, s, size_choices, save_dir, uv_name)
            
            if error:
                self.report({'WARNING'}, error)
                return {'CANCELLED'}
            
            self.report({'INFO'}, f"Multi-object rebake completed for {len(valid_objects)} object(s) as single atlas (AO→glTF Occlusion).")
        else:
            # Single-object mode: bake each object separately (original behavior)
            success_count = 0
            for obj in sel:
                if obj.mode != 'OBJECT':
                    bpy.ops.object.mode_set(mode='OBJECT')

                final_mat, error = rebake_single_object(obj, context, s, size_choices, save_dir, uv_name)
                
                if error:
                    self.report({'INFO'}, error)
                else:
                    success_count += 1

            self.report({'INFO'}, f"Rebake completed for {success_count} object(s) (AO→glTF Occlusion).")
        
        return {'FINISHED'}


# ==========================
# ===== Scene Props =========
# ==========================

def _init_props():
    s = bpy.types.Scene
    s.ar_size_choices = bpy.props.StringProperty(
        name="Size candidates (px)",
        default="128,256,512,1024,2048,4096"
    )
    s.ar_min_size = bpy.props.IntProperty(name="Min size", default=128, min=16)
    s.ar_uv_margin = bpy.props.FloatProperty(name="UV margin", default=0.002, min=0.0, max=0.05)
    s.ar_average_scale = bpy.props.BoolProperty(
        name="Average Islands Scale",
        description="Equalize UV island scale relative to their 3D geometry (ensures uniform texel density)",
        default=True
    )
    s.ar_pack_method = bpy.props.EnumProperty(
        name="UV method",
        description="PACK — unwrap+pack (Exact/Concave, Scaled margin, no-rotate); RESCALE — uniform scaling to 0..1",
        items=[
            ('PACK','PACK (unwrap+pack, no-rotate)',''),
            ('RESCALE','RESCALE (scale+translate only)',''),
        ],
        default='PACK'
    )
    s.ar_do_basecolor = bpy.props.BoolProperty(name="BaseColor", default=True)
    s.ar_do_orm = bpy.props.BoolProperty(name="ORM (R=AO,G=Rough,B=Metal)", default=True)
    s.ar_do_normal = bpy.props.BoolProperty(name="Normal", default=True)
    s.ar_multi_object = bpy.props.BoolProperty(
        name="Treat Selected as Single Mesh",
        description="Pack UV and bake textures for all selected objects as if they were one mesh (creates shared texture atlas)",
        default=False
    )
    s.ar_debug = bpy.props.BoolProperty(name="Debug print", default=False)


def _clear_props():
    s = bpy.types.Scene
    del s.ar_size_choices
    del s.ar_min_size
    del s.ar_uv_margin
    del s.ar_average_scale
    del s.ar_pack_method
    del s.ar_do_basecolor
    del s.ar_do_orm
    del s.ar_do_normal
    del s.ar_multi_object
    del s.ar_debug


classes = (ATLASREPACK_PT_panel, ATLASREPACK_OT_repack_uv, ATLASREPACK_OT_rebake_maps)


def register():
    for c in classes:
        bpy.utils.register_class(c)
    _init_props()
    log("Registered v1.5.1")


def unregister():
    _clear_props()
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
    log("Unregistered v1.5.1")


if __name__ == "__main__":
    register()

