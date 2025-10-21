# Atlas Repacker & Multi-Map Rebake

**Version**: 1.5.0  
**Author**: Viktor Kom  
**Email**: viktorcor@gmail.com  
**Blender Version**: 4.0.0 and above  
**License**: [GPL-2.0-or-later](https://www.gnu.org/licenses/gpl-2.0.html)  
**GitHub**: [ViktorCor](https://github.com/ViktorCor)

## Description
The Atlas Repacker & Multi-Map Rebake addon automates UV repacking (without rotation) and bakes BaseColor, ORM (Occlusion-Roughness-Metallic), and Normal maps into compact textures for selected mesh objects. It's designed to be glTF-friendly, routing AO to the glTF Occlusion input without multiplying it into BaseColor.

**NEW in v1.5.0**: Multi-object mode allows you to treat multiple selected objects as a single mesh for UV packing and baking, creating a shared texture atlas!

## Features
- **Multi-object mode** (NEW!): Treat multiple objects as one mesh for UV packing and baking (creates shared texture atlas)
- **Two-step workflow**: Repack UV first, then Rebake maps (allows UV inspection before baking)
- **Object-wide UV repacking** with no rotation for predictable atlas layouts
- **Smart texture sizing** based on UV coverage area
- **Multi-map baking**: BaseColor, ORM (R=AO, G=Roughness, B=Metallic), and Normal maps
- **glTF compatibility**: AO channel routes to glTF Material Output Occlusion input
- **Flexible UV methods**: PACK (unwrap+pack) or RESCALE (scale+translate only)
- **Customizable texture resolution** with candidate size list
- **Configurable UV margins** for optimal packing
- **Automatic texture packing** into .blend file
- **Debug mode** for detailed logging

## Installation
1. Download or clone this addon
2. Open Blender and go to `Edit > Preferences > Add-ons`
3. Click `Install`, select the addon folder or zip, and enable it
4. The addon will appear in the N-panel under `Atlas Repacker` tab

## Usage

### Multi-Object Mode (NEW!)

When enabled, the addon treats all selected objects as if they were a single mesh:
- **UV packing**: All objects' UV islands are packed together in one atlas
- **Baking**: Creates temporary combined mesh, bakes textures, then assigns the result to all original objects

This is perfect for creating optimized texture atlases for game assets or for consolidating multiple objects into a single material.

### Two-Step Workflow

#### Step 1: Repack UV
1. Select one or more mesh objects with existing UV maps
2. Open the N-panel in the 3D Viewport and navigate to `Atlas Repacker` tab
3. **Enable "Treat Selected as Single Mesh"** if you want to pack multiple objects together
4. Configure UV settings:
   - **UV margin**: Spacing between UV islands (default: 0.002)
   - **UV method**: Choose PACK (copy UV + pack islands) or RESCALE (scale+translate)
5. Click `Repack UV` button
6. **Check the result** in UV Editor - you can adjust settings and repack if needed

#### Step 2: Rebake Maps
1. With the same objects selected, configure baking settings:
   - **Size candidates**: Comma-separated texture resolutions (e.g., `128,256,512,1024,2048,4096`)
   - **Min size**: Minimum texture resolution
   - **Select maps to bake**: BaseColor, ORM, Normal
2. Click `Rebake Maps` button
3. The addon will:
   - In multi-object mode: Create temporary combined mesh, bake, and assign result to all objects
   - In single-object mode: Bake each object separately
   - Verify that BakedUV layer exists (created in Step 1)
   - Calculate optimal texture resolution based on UV coverage
   - Bake selected maps from original UV to new BakedUV
   - Automatically pack textures into .blend file
   - Create a simplified material with glTF-compatible connections

## Technical Details
- Uses Cycles render engine for baking (1 sample)
- Automatically detects material roles (BaseColor, Normal, ORM)
- Preserves original materials during baking process (creates temporary copies)
- Creates final material with proper glTF node connections
- UV packing options: Concave shape method, scaled margins, closest UDIM
- Multi-object baking: Creates duplicates, joins them temporarily, bakes, then assigns to originals

## Maintainer
Viktor Kom  
Email: viktorcor@gmail.com  
GitHub: [@ViktorCor](https://github.com/ViktorCor)

