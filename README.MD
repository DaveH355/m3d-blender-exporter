# Blender .m3d  exporter
This is forked Blender addon for exporting models in the .m3d format. 
The original source and docs are at https://gitlab.com/bztsrc/model3d

Last tested with Blender 3.6.3

## ChangeLog
* Added an export option to allow UV coordinates outside the 0-1 range
* Removed Write Shapes export option as it appears to be unused 
* Textures will be checked in the .blend file as well as system. 
* Packed textures will be automatically converted to png if needed

## Contribute
Are you looking to develop Blender addons? Here's a good workflow

Open up your favorite IDE and pip install a Blender api stubs module for code completion

1. https://github.com/nutti/fake-bpy-module
2. https://github.com/mysticfall/bpystubgen


Create a blender script that will load and enable your addon. You can use the one below! 

```python
import bpy

# Replace with name of addon file
addon_name = 'm3d'
# Replace with absolute path to addon
addon_path = "path/to/m3d.py"

# Unload old addon if exists
if addon_name in bpy.context.preferences.addons.keys():
    bpy.ops.preferences.addon_remove(module=addon_name)

# Load the addon
bpy.ops.preferences.addon_install(filepath=addon_path)
bpy.ops.preferences.addon_enable(module=addon_name)
```


