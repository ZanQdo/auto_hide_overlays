import bpy
from bpy.app.handlers import persistent

# ------------------------------------------------------------------------
#    Helpers: Shared Hide/Restore Logic
# ------------------------------------------------------------------------

def apply_hide(scene, overlay):
    """
    Hides overlays based on scene settings.
    Returns: (restore_data, restore_global)
    """
    restore_data = {}
    restore_global = False

    if scene.auto_hide_strategy == 'ALL':
        restore_global = True
        restore_data["show_overlays"] = overlay.show_overlays
        overlay.show_overlays = False
        
    elif scene.auto_hide_strategy == 'CUSTOM':
        restore_global = False
        
        # Define mapping: (Scene Property, Overlay Attribute)
        properties_to_check = [
            ("auto_hide_bones", "show_bones"),
            ("auto_hide_wireframes", "show_wireframes"),
            ("auto_hide_extras", "show_extras"),
            ("auto_hide_text", "show_text"),
            ("auto_hide_cursor", "show_cursor"),
            ("auto_hide_relationship_lines", "show_relationship_lines"),
        ]
        
        for scene_prop, overlay_attr in properties_to_check:
            # If user wants to hide this specific element
            if getattr(scene, scene_prop, False):
                # Check if the overlay has this attribute (safety for different Blender versions/contexts)
                if hasattr(overlay, overlay_attr):
                    # Store current state
                    restore_data[overlay_attr] = getattr(overlay, overlay_attr)
                    # Turn it off
                    setattr(overlay, overlay_attr, False)
                    
    return restore_data, restore_global

def apply_restore(overlay, restore_data, restore_global):
    """
    Restores overlays from the saved data.
    """
    if restore_global:
        if "show_overlays" in restore_data:
            overlay.show_overlays = restore_data["show_overlays"]
    else:
        for attr, val in restore_data.items():
            if hasattr(overlay, attr):
                setattr(overlay, attr, val)

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
    _space_data = None
    _restore_data = {} 
    _restore_global = False 

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
                overlay = self._space_data.overlay
                apply_restore(overlay, self._restore_data, self._restore_global)
            
            return {'FINISHED', 'PASS_THROUGH'}
        
        return {'PASS_THROUGH'}

    def invoke(self, context, event):
        # 1. Check if the feature is enabled in the UI
        scene = context.scene
        if not scene.auto_hide_overlays:
            # Feature is disabled: Just run the normal transform and exit
            self.execute_transform()
            return {'FINISHED'}

        # 2. Ensure we are in a 3D View
        if context.space_data.type == 'VIEW_3D':
            self._space_data = context.space_data
            
            # 3. Apply Hide Strategy (Shared Logic)
            overlay = self._space_data.overlay
            self._restore_data, self._restore_global = apply_hide(scene, overlay)
            
        else:
            self.report({'WARNING'}, "Not in View3D")
            return {'CANCELLED'}

        # 4. Call the Native Transform Operator
        self.execute_transform()

        # 5. Start our monitoring modal
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

# ------------------------------------------------------------------------
#    Handler: Auto Hide Playback
# ------------------------------------------------------------------------

# Global state to track playback hiding
_playback_state = {
    "active": False,
    "views": []  # List of dicts: { 'overlay': obj, 'data': {}, 'global': bool }
}

def _hide_all_views(scene):
    """Finds all visible 3D views and hides overlays."""
    global _playback_state
    
    # Avoid double hiding
    if _playback_state["active"]:
        return

    _playback_state["active"] = True
    _playback_state["views"] = []
    
    # Iterate all windows and areas to find 3D Views
    wm = bpy.context.window_manager
    if not wm:
        return

    for window in wm.windows:
        for area in window.screen.areas:
            if area.type == 'VIEW_3D':
                for space in area.spaces:
                    if space.type == 'VIEW_3D':
                        overlay = space.overlay
                        # Apply hide and store restoration data
                        r_data, r_global = apply_hide(scene, overlay)
                        _playback_state["views"].append({
                            "overlay": overlay,
                            "data": r_data,
                            "global": r_global
                        })

def _restore_all_views():
    """Restores overlays on all tracked views."""
    global _playback_state
    
    if not _playback_state["active"]:
        return
        
    for view_record in _playback_state["views"]:
        overlay = view_record["overlay"]
        # Check if overlay is still valid (area might be closed)
        try:
            apply_restore(overlay, view_record["data"], view_record["global"])
        except:
            pass 
            
    # Reset State
    _playback_state["active"] = False
    _playback_state["views"] = []

@persistent
def on_playback_start(scene):
    """Handler called when animation playback starts."""
    # Ensure we have the correct scene context
    target_scene = scene if isinstance(scene, bpy.types.Scene) else bpy.context.scene
    
    if getattr(target_scene, "auto_hide_playback", False):
        _hide_all_views(target_scene)

@persistent
def on_playback_stop(scene):
    """Handler called when animation playback stops."""
    _restore_all_views()

def update_auto_hide_playback(self, context):
    """Callback for when the user toggles the property manually."""
    # If the user toggles the checkbox WHILE animation is playing
    if context.screen.is_animation_playing:
        if self.auto_hide_playback:
            _hide_all_views(context.scene)
        else:
            _restore_all_views()

# ------------------------------------------------------------------------
#    UI: Overlay Menu
# ------------------------------------------------------------------------

def draw_overlay_menu(self, context):
    layout = self.layout
    scene = context.scene
    
    # Add a separator and our property at the bottom of the Overlay popover
    layout.separator()
    
    layout.label(text="Auto Hide Overlays")

    # Main Toggles
    col = layout.column(align=True)
    col.prop(scene, "auto_hide_overlays", text="During Transform")
    col.prop(scene, "auto_hide_playback", text="During Playback")
    
    # Granular Options (if either is enabled)
    if scene.auto_hide_overlays or scene.auto_hide_playback:
        col = layout.column(align=True)
        # Strategy Selector
        col.row().prop(scene, "auto_hide_strategy", expand=True)
        
        # Custom Checkboxes
        if scene.auto_hide_strategy == 'CUSTOM':
            box = col.box()
            col_box = box.column(align=True)
            col_box.prop(scene, "auto_hide_bones", text="Bones")
            col_box.prop(scene, "auto_hide_wireframes", text="Wireframes")
            col_box.prop(scene, "auto_hide_extras", text="Extras")
            col_box.prop(scene, "auto_hide_relationship_lines", text="Relationships")
            col_box.prop(scene, "auto_hide_text", text="Text Info")
            col_box.prop(scene, "auto_hide_cursor", text="3D Cursor")

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
    
    # 1. Main Toggles
    bpy.types.Scene.auto_hide_overlays = bpy.props.BoolProperty(
        name="Auto Hide During Transform",
        description="Hide viewport overlays while transforming (G/R/S)",
        default=False
    )
    
    bpy.types.Scene.auto_hide_playback = bpy.props.BoolProperty(
        name="Auto Hide During Playback",
        description="Hide viewport overlays while animation is playing",
        default=False,
        update=update_auto_hide_playback
    )
    
    # 2. Strategy Enum
    bpy.types.Scene.auto_hide_strategy = bpy.props.EnumProperty(
        name="Strategy",
        description="Choose what to hide",
        items=[
            ('ALL', "Hide All", "Hide all overlays globally"),
            ('CUSTOM', "Custom", "Hide specific overlay elements"),
        ],
        default='ALL'
    )
    
    # 3. Custom Granular Properties
    bpy.types.Scene.auto_hide_bones = bpy.props.BoolProperty(name="Hide Bones", default=True)
    bpy.types.Scene.auto_hide_wireframes = bpy.props.BoolProperty(name="Hide Wireframes", default=True)
    bpy.types.Scene.auto_hide_extras = bpy.props.BoolProperty(name="Hide Extras", default=True)
    bpy.types.Scene.auto_hide_text = bpy.props.BoolProperty(name="Hide Text", default=False)
    bpy.types.Scene.auto_hide_cursor = bpy.props.BoolProperty(name="Hide Cursor", default=False)
    bpy.types.Scene.auto_hide_relationship_lines = bpy.props.BoolProperty(name="Hide Relationships", default=False)
    
    # Add UI to Overlay Menu
    bpy.types.VIEW3D_PT_overlay.append(draw_overlay_menu)
    
    # Register Playback Handlers
    bpy.app.handlers.animation_playback_pre.append(on_playback_start)
    bpy.app.handlers.animation_playback_post.append(on_playback_stop)
    
    register_keymaps()

def unregister():
    unregister_keymaps()
    
    # Remove Handlers
    if on_playback_start in bpy.app.handlers.animation_playback_pre:
        bpy.app.handlers.animation_playback_pre.remove(on_playback_start)
    if on_playback_stop in bpy.app.handlers.animation_playback_post:
        bpy.app.handlers.animation_playback_post.remove(on_playback_stop)
    
    # Remove UI
    bpy.types.VIEW3D_PT_overlay.remove(draw_overlay_menu)
    
    # Remove Properties
    del bpy.types.Scene.auto_hide_overlays
    del bpy.types.Scene.auto_hide_playback
    del bpy.types.Scene.auto_hide_strategy
    del bpy.types.Scene.auto_hide_bones
    del bpy.types.Scene.auto_hide_wireframes
    del bpy.types.Scene.auto_hide_extras
    del bpy.types.Scene.auto_hide_text
    del bpy.types.Scene.auto_hide_cursor
    del bpy.types.Scene.auto_hide_relationship_lines
    
    bpy.utils.unregister_class(OT_AutoHideTransform)

if __name__ == "__main__":
    register()