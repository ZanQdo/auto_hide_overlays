import bpy

# ------------------------------------------------------------------------
#    Operator: Auto Hide Overlays Transform Wrapper
# ------------------------------------------------------------------------

class OT_AutoHideTransform(bpy.types.Operator):
    """Hides overlays while transforming, then restores them."""
    bl_idname = "view3d.auto_hide_transform"
    bl_label = "Auto Hide Transform"
    bl_options = {'REGISTER', 'UNDO'}

    # Property to decide which underlying transform operator to call
    mode: bpy.props.EnumProperty(
        items=[
            ('TRANSLATE', "Translate", "Move"),
            ('ROTATE', "Rotate", "Rotate"),
            ('RESIZE', "Scale", "Scale"),
        ],
        name="Mode",
        default='TRANSLATE'
    )

    # Internal state variables
    _initial_overlay_state = True
    _space_data = None

    def execute_transform(self):
        """Helper to call the native transform operator."""
        try:
            if self.mode == 'TRANSLATE':
                bpy.ops.transform.translate('INVOKE_DEFAULT')
            elif self.mode == 'ROTATE':
                bpy.ops.transform.rotate('INVOKE_DEFAULT')
            elif self.mode == 'RESIZE':
                bpy.ops.transform.resize('INVOKE_DEFAULT')
        except RuntimeError as e:
            print(f"Transform Error: {e}")

    def modal(self, context, event):
        # Events that confirm or cancel the operation
        finish_events = {'LEFTMOUSE', 'RIGHTMOUSE', 'ESC', 'RET', 'NUMPAD_ENTER'}
        
        if event.type in finish_events and event.value == 'RELEASE':
            # Restore the overlay state
            if self._space_data:
                self._space_data.overlay.show_overlays = self._initial_overlay_state
            
            return {'FINISHED', 'PASS_THROUGH'}
        
        return {'PASS_THROUGH'}

    def invoke(self, context, event):
        # 1. Check if the feature is enabled in the UI
        if not context.scene.auto_hide_overlays:
            # Feature is disabled: Just run the normal transform and exit
            self.execute_transform()
            return {'FINISHED'}

        # 2. Ensure we are in a 3D View
        if context.space_data.type == 'VIEW_3D':
            self._space_data = context.space_data
            self._initial_overlay_state = self._space_data.overlay.show_overlays
            
            # 3. Hide Overlays
            self._space_data.overlay.show_overlays = False
        else:
            self.report({'WARNING'}, "Not in View3D")
            return {'CANCELLED'}

        # 4. Call the Native Transform Operator
        self.execute_transform()

        # 5. Start our monitoring modal
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

# ------------------------------------------------------------------------
#    UI: Overlay Menu
# ------------------------------------------------------------------------

def draw_overlay_menu(self, context):
    layout = self.layout
    # Add a separator and our property at the bottom of the Overlay popover
    layout.separator()
    layout.prop(context.scene, "auto_hide_overlays", text="Auto Hide During Transform")

# ------------------------------------------------------------------------
#    Keymap Registration
# ------------------------------------------------------------------------

addon_keymaps = []

def register_keymaps():
    wm = bpy.context.window_manager
    kc = wm.keyconfigs.addon
    if not kc:
        return

    # Helper to add keymap items
    def add_km(km_name, space_type):
        km = kc.keymaps.new(name=km_name, space_type=space_type)
        
        # G - Translate
        kmi = km.keymap_items.new(OT_AutoHideTransform.bl_idname, 'G', 'PRESS')
        kmi.properties.mode = 'TRANSLATE'
        addon_keymaps.append((km, kmi))
        
        # R - Rotate
        kmi = km.keymap_items.new(OT_AutoHideTransform.bl_idname, 'R', 'PRESS')
        kmi.properties.mode = 'ROTATE'
        addon_keymaps.append((km, kmi))
        
        # S - Scale
        kmi = km.keymap_items.new(OT_AutoHideTransform.bl_idname, 'S', 'PRESS')
        kmi.properties.mode = 'RESIZE'
        addon_keymaps.append((km, kmi))

    # Register for Object Mode and Pose Mode
    add_km('Object Mode', 'EMPTY')
    add_km('Pose', 'EMPTY')

def unregister_keymaps():
    for km, kmi in addon_keymaps:
        km.keymap_items.remove(kmi)
    addon_keymaps.clear()

# ------------------------------------------------------------------------
#    Registration
# ------------------------------------------------------------------------

def register():
    bpy.utils.register_class(OT_AutoHideTransform)
    
    # Register Property
    bpy.types.Scene.auto_hide_overlays = bpy.props.BoolProperty(
        name="Auto Hide Overlays",
        description="Hide viewport overlays while transforming (G/R/S)",
        default=True
    )
    
    # Add UI to Overlay Menu
    # We append to VIEW3D_PT_overlay so it shows up in the popover
    bpy.types.VIEW3D_PT_overlay.append(draw_overlay_menu)
    
    register_keymaps()

def unregister():
    unregister_keymaps()
    
    # Remove UI
    bpy.types.VIEW3D_PT_overlay.remove(draw_overlay_menu)
    
    del bpy.types.Scene.auto_hide_overlays
    bpy.utils.unregister_class(OT_AutoHideTransform)

if __name__ == "__main__":
    register()