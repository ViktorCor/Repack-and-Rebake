# Atlas Repacker & Multi-Map Rebake

**Version**: 1.5.3  
**Author**: Viktor Kom  
**Email**: viktorcor@gmail.com  
**Blender Version**: 4.0.0 and above  
**License**: [GPL-2.0-or-later](https://www.gnu.org/licenses/gpl-2.0.html)  
**GitHub**: [ViktorCor](https://github.com/ViktorCor)

## Description
The Atlas Repacker & Multi-Map Rebake addon automates UV repacking (without rotation) and bakes BaseColor, ORM (Occlusion-Roughness-Metallic), and Normal maps into compact textures for selected mesh objects. It's designed to be glTF-friendly, routing AO to the glTF Occlusion input without multiplying it into BaseColor.

## Features
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

### Two-Step Workflow

#### Step 1: Repack UV
1. Select one or more mesh objects with existing UV maps
2. Open the N-panel in the 3D Viewport and navigate to `Atlas Repacker` tab
3. Configure UV settings:
   - **UV margin**: Spacing between UV islands (default: 0.002)
   - **UV method**: Choose PACK (copy UV + pack islands) or RESCALE (scale+translate)
4. Click `Repack UV` button
5. **Check the result** in UV Editor - you can adjust settings and repack if needed

#### Step 2: Rebake Maps
1. With the same objects selected, configure baking settings:
   - **Size candidates**: Comma-separated texture resolutions (e.g., `128,256,512,1024,2048,4096`)
   - **Min size**: Minimum texture resolution
   - **Select maps to bake**: BaseColor, ORM, Normal
2. Click `Rebake Maps` button
3. The addon will:
   - Verify that BakedUV layer exists (created in Step 1)
   - Calculate optimal texture resolution based on UV coverage
   - Bake selected maps from original UV to new BakedUV
   - Automatically pack textures into .blend file
   - Create a simplified material with glTF-compatible connections

## Technical Details
- Uses Cycles render engine for baking (1 sample)
- Automatically detects material roles (BaseColor, Normal, ORM)
- Preserves original materials during baking process
- Creates final material with proper glTF node connections
- UV packing options: Concave shape method, scaled margins, closest UDIM

## Maintainer
Viktor Kom  
Email: viktorcor@gmail.com  
GitHub: [@ViktorCor](https://github.com/ViktorCor)

