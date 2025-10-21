bl_info = {
    "name": "Atlas Repacker & Multi-Map Rebake",
    "author": "Viktor Kom",
    "version": (1, 5, 1),
    "blender": (4, 0, 0),
    "location": "View3D > N-panel > Atlas Repacker",
    "description": "UV repack (no rotate), bake BaseColor/ORM/Normal into compact textures, object-wide, glTF-friendly (AO→glTF Occlusion). Supports multi-object mode.",
    "category": "UV",
}

from . import repack_rebake


def register():
    repack_rebake.register()


def unregister():
    repack_rebake.unregister()


if __name__ == "__main__":
    register()


