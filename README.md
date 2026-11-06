# IDVMI-Tools

A Blender add-on for 3DMigoto and proprietary game engine modding, developed for a doll-dressing PvP game.

Provides tools for asset lookup, extraction, mesh and animation import/export, material setup, and modding workflow automation.

## Blender Version
- Blender 3.6

## Features

### 3DMigoto
- **Mesh Importer:** Imports `.ib`, `vb0*.txt`, and character `.buf` files.
- **Automated Asset Detection:** Identifies 3D models while excluding 2D/UI elements. *(Outdated since proprietary engine version 3.)*
- **Auto-Texture Assignment:** Automatically assigns textures to materials.
- **Pose Importer:** Imports pose data from `cb.txt` and `cb.buf` files.
- **Mod Exporter:** Automates `.ini` file generation for the mod loader.

### Proprietary Game Engine
- **Asset Lookup & Extraction:** Locates assets using the game's lookup data and extracts them from game archives.
- **Mesh Importer/Exporter:** Supports proprietary binary mesh formats, skeletons, coordinate system conversions, bone transformations, and LOD handling.
- **Animation Importer/Exporter:** Imports and exports skeletal animations, including compressed quaternion rotations and FP16 position/scale data.
- **Armature Tools:** Supports custom armature exporting, bone mapping, and socket operations.
- **Material Automation:** Imports baked-lighting textures and automatically configures Blender material nodes.
- **Mod Exporter:** Exports modified assets for use with the game's proprietary engine.

## Preview

<img width="1509" height="810" alt="IDVMI-Tools Blender add-on preview" src="https://github.com/user-attachments/assets/7677736c-3c73-4065-98b6-519d28a3be1a" />
