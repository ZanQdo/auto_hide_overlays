import bpy
import rna_keymap_ui
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
    props = scene.auto_hide

    if props.strategy == 'ALL':
        restore_global = True
        restore_data["show_overlays"] = overlay.show_overlays
        overlay.show_overlays = False
        
    elif props.strategy == 'CUSTOM':
        restore_global = False
        
        # Define mapping: (PropertyGroup Attribute, Overlay Attribute)
        properties_to_check = [
            ("bones", "show_bones"),
            ("wireframes", "show_wireframes"),
            ("outline", "show_outline_selected"),
            ("extras", "show_extras"),
            ("origins", "show_object_origins"),
            ("origins", "show_object_origins_all"),
            ("face_orientation", "show_face_orientation"),
            ("text", "show_text"),
            ("stats", "show_stats"),
            ("cursor", "show_cursor"),
            ("relationship_lines", "show_relationship_lines"),
            ("floor", "show_floor"),
            ("axes", "show_axis_x"),
            ("axes", "show_axis_y"),
            ("axes", "show_axis_z"),
        ]
        
        for prop_name, overlay_attr in properties_to_check:
            # If user wants to hide this specific element
            if getattr(props, prop_name, False):
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
    _restore_panel_data = {}

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
                
                # Restore panels
                if self._restore_panel_data:
                    try:
                        if "show_region_ui" in self._restore_panel_data:
                            self._space_data.show_region_ui = self._restore_panel_data["show_region_ui"]
                        if "show_region_toolbar" in self._restore_panel_data:
                            self._space_data.show_region_toolbar = self._restore_panel_data["show_region_toolbar"]
                    except (AttributeError, TypeError, ValueError, ReferenceError):
                        pass
            
            return {'FINISHED', 'PASS_THROUGH'}
        
        return {'PASS_THROUGH'}

    def invoke(self, context, event):
        # Reset states for this execution
        self._restore_data = {}
        self._restore_global = False
        self._restore_panel_data = {}

        # 1. Check if the feature is enabled in the UI
        scene = context.scene
        if not (scene.auto_hide.overlays or scene.auto_hide.transform_panels):
            # Feature is disabled: Just run the normal transform and exit
            self.execute_transform()
            return {'FINISHED'}

        # 2. Ensure we are in a 3D View
        if context.space_data.type == 'VIEW_3D':
            self._space_data = context.space_data
            
            # 3. Apply Hide Strategy
            if scene.auto_hide.overlays:
                overlay = self._space_data.overlay
                self._restore_data, self._restore_global = apply_hide(scene, overlay)
            
            # Apply Panel Hide Strategy
            if scene.auto_hide.transform_panels:
                self._restore_panel_data["show_region_ui"] = self._space_data.show_region_ui
                self._restore_panel_data["show_region_toolbar"] = self._space_data.show_region_toolbar
                self._space_data.show_region_ui = False
                self._space_data.show_region_toolbar = False
            
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
    "views": []  # List of dicts with space/overlay details
}

def _hide_all_views(scene):
    """Finds all visible 3D views and hides requested elements."""
    global _playback_state
    
    # Avoid double hiding
    if _playback_state["active"]:
        return

    _playback_state["active"] = True
    _playback_state["views"] = []
    
    wm = bpy.context.window_manager
    if not wm:
        return

    hide_overlays = getattr(scene.auto_hide, "playback", False)
    hide_panels = getattr(scene.auto_hide, "playback_panels", False)

    for window in wm.windows:
        for area in window.screen.areas:
            if area.type == 'VIEW_3D':
                for space in area.spaces:
                    if space.type == 'VIEW_3D':
                        view_record = {
                            "space": space,
                            "overlay": space.overlay,
                            "data": {},
                            "global": False,
                            "panel_data": {}
                        }
                        
                        # Apply Overlay Hide
                        if hide_overlays:
                            r_data, r_global = apply_hide(scene, space.overlay)
                            view_record["data"] = r_data
                            view_record["global"] = r_global
                            
                        # Apply Panel Hide
                        if hide_panels:
                            view_record["panel_data"]["show_region_ui"] = space.show_region_ui
                            view_record["panel_data"]["show_region_toolbar"] = space.show_region_toolbar
                            space.show_region_ui = False
                            space.show_region_toolbar = False
                            
                        _playback_state["views"].append(view_record)

def _restore_all_views():
    """Restores overlays and panels on all tracked views."""
    global _playback_state
    
    if not _playback_state["active"]:
        return
        
    for view_record in _playback_state["views"]:
        # 1. Restore Overlays
        overlay = view_record["overlay"]
        try:
            if view_record["data"] or view_record["global"]:
                apply_restore(overlay, view_record["data"], view_record["global"])
        except (AttributeError, TypeError, ValueError, ReferenceError):
            pass 
            
        # 2. Restore Panels
        space = view_record.get("space")
        panel_data = view_record.get("panel_data", {})
        if space and panel_data:
            try:
                if "show_region_ui" in panel_data:
                    space.show_region_ui = panel_data["show_region_ui"]
                if "show_region_toolbar" in panel_data:
                    space.show_region_toolbar = panel_data["show_region_toolbar"]
            except (AttributeError, TypeError, ValueError, ReferenceError):
                pass

    # Reset State
    _playback_state["active"] = False
    _playback_state["views"] = []

@persistent
def on_playback_start(scene):
    """Handler called when animation playback starts."""
    target_scene = scene if isinstance(scene, bpy.types.Scene) else bpy.context.scene
    auto_hide = target_scene.auto_hide
    
    if auto_hide.playback or auto_hide.playback_panels:
        _hide_all_views(target_scene)

@persistent
def on_playback_stop(scene):
    """Handler called when animation playback stops."""
    _restore_all_views()

def update_auto_hide_playback(self, context):
    """Callback for when the user toggles the property manually."""
    if context.screen.is_animation_playing:
        # Re-initialize to ensure newly enabled items are hidden immediately
        _restore_all_views()
        if self.playback or self.playback_panels:
            _hide_all_views(context.scene)
    else:
        _restore_all_views()

# ------------------------------------------------------------------------
#    Property Group (Bundled Properties)
# ------------------------------------------------------------------------

class AutoHideProperties(bpy.types.PropertyGroup):
    """Property group for all Auto Hide settings"""
    
    overlays: bpy.props.BoolProperty(
        name="Auto Hide During Transform",
        description="Hide viewport overlays while transforming (G/R/S)",
        default=False
    )
    
    transform_panels: bpy.props.BoolProperty(
        name="Hide Sidebar/Toolbar During Transform",
        description="Hide Toolbar and Sidebar panels while transforming",
        default=False
    )
    
    playback: bpy.props.BoolProperty(
        name="Hide Overlays",
        description="Hide viewport overlays while animation is playing",
        default=False,
        update=update_auto_hide_playback
    )
    
    playback_panels: bpy.props.BoolProperty(
        name="Hide Sidebar/Toolbar",
        description="Hide Toolbar and Sidebar panels while animation is playing",
        default=False,
        update=update_auto_hide_playback
    )
    
    strategy: bpy.props.EnumProperty(
        name="Strategy",
        description="Choose what to hide",
        items=[
            ('ALL', "Hide All", "Hide all overlays globally"),
            ('CUSTOM', "Custom", "Hide specific overlay elements"),
        ],
        default='ALL'
    )
    
    bones: bpy.props.BoolProperty(name="Hide Bones", default=True)
    wireframes: bpy.props.BoolProperty(name="Hide Wireframes", default=True)
    outline: bpy.props.BoolProperty(name="Hide Outline", default=True)
    extras: bpy.props.BoolProperty(name="Hide Extras", default=True)
    origins: bpy.props.BoolProperty(name="Hide Origins", default=True)
    face_orientation: bpy.props.BoolProperty(name="Hide Face Orientation", default=True)
    text: bpy.props.BoolProperty(name="Hide Text", default=True)
    stats: bpy.props.BoolProperty(name="Hide Statistics", default=True)
    cursor: bpy.props.BoolProperty(name="Hide Cursor", default=True)
    relationship_lines: bpy.props.BoolProperty(name="Hide Relationships", default=True)
    floor: bpy.props.BoolProperty(name="Hide Grid Floor", default=True)
    axes: bpy.props.BoolProperty(name="Hide Axes", default=True)

# ------------------------------------------------------------------------
#    Addon Preferences
# ------------------------------------------------------------------------

class AutoHidePreferences(bpy.types.AddonPreferences):
    """Preferences for the Auto Hide Addon to customize hotkeys"""
    bl_idname = __package__ if __package__ else __name__

    def draw(self, context):
        layout = self.layout
        
        # Instructions Explaining Key Configurations
        box = layout.box()
        box.label(text="Transform Shortcut Overrides", icon='INFO')
        box.label(text="To hide overlays during transforms, this add-on intercepts the Move, Rotate, and Scale tools.")
        box.label(text="If you use custom hotkeys for these tools, please update the mappings below to match them.")
        
        layout.separator()
        
        wm = context.window_manager
        kc = wm.keyconfigs.user
        
        if not kc:
            layout.label(text="Keymap config not available.")
            return
            
        # Group keymaps by their context
        km_dict = {}
        for km, kmi in addon_keymaps:
            if km.name not in km_dict:
                km_dict[km.name] = []
            km_dict[km.name].append((km, kmi))
        
        mode_labels = {
            'TRANSLATE': "Translate (Move)",
            'ROTATE': "Rotate",
            'RESIZE': "Scale"
        }
            
        for km_name, items in km_dict.items():
            box = layout.box()
            box.label(text=f"Context: {km_name}", icon='KEYINGSET')
            
            for km, kmi in items:
                mode = kmi.properties.mode
                display_name = mode_labels.get(mode, mode.title())
                
                col = box.column(align=True)
                col.label(text=display_name)
                
                col.context_pointer_set("keymap", km)
                rna_keymap_ui.draw_kmi(
                    ["ADDON", "USER", "DEFAULT"], kc, km, kmi, col, 0
                )
                box.separator()

# ------------------------------------------------------------------------
#    UI: Overlay Menu
# ------------------------------------------------------------------------

def draw_overlay_menu(self, context):
    layout = self.layout
    props = context.scene.auto_hide
    
    layout.separator()
    layout.label(text="Auto Hide Properties")

    # Main Toggles
    col = layout.column(align=True)
    col.label(text="Transform:")
    col.prop(props, "overlays", text="Hide Overlays")
    col.prop(props, "transform_panels", text="Hide Sidebar/Toolbar")
    
    col.separator()
    col.label(text="Playback:")
    col.prop(props, "playback", text="Hide Overlays")
    col.prop(props, "playback_panels", text="Hide Sidebar/Toolbar")
    
    # Granular Options (if either overlay auto-hiding is enabled)
    if props.overlays or props.playback:
        col.separator()
        col = layout.column(align=True)
        # Strategy Selector
        col.row().prop(props, "strategy", expand=True)
        
        # Custom Checkboxes
        if props.strategy == 'CUSTOM':
            box = col.box()
            col_box = box.column(align=True)
            col_box.prop(props, "bones", text="Bones")
            col_box.prop(props, "wireframes", text="Wireframes")
            col_box.prop(props, "outline", text="Outline")
            col_box.prop(props, "extras", text="Extras")
            col_box.prop(props, "origins", text="Origins")
            col_box.prop(props, "face_orientation", text="Face Orientation")
            col_box.prop(props, "relationship_lines", text="Relationships")
            col_box.prop(props, "text", text="Text Info")
            col_box.prop(props, "stats", text="Statistics")
            col_box.prop(props, "cursor", text="3D Cursor")
            col_box.prop(props, "floor", text="Grid Floor")
            col_box.prop(props, "axes", text="Axes")

# ------------------------------------------------------------------------
#    Keymap Registration
# ------------------------------------------------------------------------

addon_keymaps = []

def register_keymaps():
    wm = bpy.context.window_manager
    kc = wm.keyconfigs.addon
    if not kc:
        return

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
    # Classes
    bpy.utils.register_class(OT_AutoHideTransform)
    bpy.utils.register_class(AutoHideProperties)
    bpy.utils.register_class(AutoHidePreferences)
    
    # Assign the PropertyGroup pointer to the Scene
    bpy.types.Scene.auto_hide = bpy.props.PointerProperty(type=AutoHideProperties)
    
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
    
    # Remove the PropertyGroup pointer and Classes
    del bpy.types.Scene.auto_hide
    bpy.utils.unregister_class(AutoHidePreferences)
    bpy.utils.unregister_class(AutoHideProperties)
    bpy.utils.unregister_class(OT_AutoHideTransform)

if __name__ == "__main__":
    register()