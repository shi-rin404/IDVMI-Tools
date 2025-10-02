import bpy, os, tempfile, shutil, json

# ---------- ORTAK: Güvenli klasör doğrulama ----------
def _abspath(p):  # .blend'e göre çöz
    return bpy.path.abspath(p) if p else p

def _ensure_dir_ok(path, *, must_exist=True, must_be_writable=False):
    if not path:
        raise ValueError("Klasör yolu boş.")
    ap = _abspath(path)
    if must_exist and not os.path.isdir(ap):
        raise FileNotFoundError(f"Geçersiz klasör: {ap}")
    if must_be_writable:
        # yazılabilir mi test et
        try:
            fd, testpath = tempfile.mkstemp(prefix="idvmi_", dir=ap)
            os.close(fd); os.remove(testpath)
        except Exception as e:
            raise PermissionError(f"Yazma izni yok: {ap} ({e})")
    return ap


# ---------- OP: Extract ----------
class IDVMI_OT_extract_frame_dump(bpy.types.Operator):
    bl_idname = "idvmi_tools.extract_frame_dump"
    bl_label = "Extract Frame Dump"

    def execute(self, context):
        try:
            folder_path = _ensure_dir_ok(context.scene.frame_dump_selector, must_exist=True, must_be_writable=True)
            # TODO: burada gerçek extract fonksiyonunu çağır
            extract_frame_dump(folder_path)
            self.report({'INFO'}, f"Extract OK → {folder_path}")
            return {'FINISHED'}
        except Exception as e:
            self.report({'ERROR'}, f"Extract Error: {e}")
            return {'CANCELLED'}
        
def extract_frame_dump(folder_path):
    files = os.listdir(folder_path)

    matched_character_draws = {}

    for item in files:
        if any(cb_hash in item for cb_hash in ("vs-cb0=06450352", "ps-cb0=e2545ad6")):
            draw_order = item.split("-", 1)[0].split(".", 1)[0]

            matched_character_draws[draw_order] = 1 if draw_order not in matched_character_draws else 2

    chosen_character_draws = []

    for key in matched_character_draws:
        if matched_character_draws[key] == 2:
            chosen_character_draws.append(key)

    character_materials = []

    for item in files:
        if not any(item.startswith(draw_order) for draw_order in chosen_character_draws):
            continue

        if any(tex_slot in item for tex_slot in ("t0", "t9", "t10", "t11", "vb0=", "ib=")):
            character_materials.append(item)

    texture_usage = {}

    for item in character_materials:
        draw_order = item.split(".", 1)[0].split("-", 1)[0]
        texture_slot = item.split("=", 1)[0].rsplit("-", 1)[1]
        texture_hash = item.split(texture_slot, 1)[1].split("-", 1)[0].split(".")[0].replace("=", "")

        if not draw_order in texture_usage:
            texture_usage[draw_order] = {}

        texture_usage[draw_order][texture_slot] = texture_hash

    shadow_draw_calls = []

    for draw_order in texture_usage:
        if "t0" in texture_usage[draw_order]:
            if texture_usage[draw_order]["t0"] == "26ba2a16": # It's shadow hash
                shadow_draw_calls.append(draw_order)

    for shadow_call in shadow_draw_calls:
        del texture_usage[shadow_call]

    character_materials_set = {}

    for item in character_materials:
        draw_order = item.split(".", 1)[0].split("-", 1)[0]

        if draw_order in shadow_draw_calls:
            continue

        texture_slot = item.split("=", 1)[0].rsplit("-", 1)[1]
        texture_hash = item.split(texture_slot, 1)[1].split("-", 1)[0].replace("=","")
        
        if not texture_hash in character_materials_set:
            character_materials_set[texture_hash] = [texture_slot]

        character_materials_set[texture_hash].append(item)

    if not os.path.isdir(os.path.join(folder_path, "Character")):
        os.mkdir(os.path.join(folder_path, "Character"))

    if character_materials:
        with open(os.path.join(folder_path, "Character", "TextureUsage.json"), "w") as f:
            json.dump(texture_usage, f, indent=4)

        for hash in character_materials_set:
            if "dds" in character_materials_set[hash][1]:                
                shutil.copyfile(os.path.join(folder_path, character_materials_set[hash][1]), os.path.join(folder_path, "Character", f"ps-{character_materials_set[hash][0]}={hash}.dds"))
            elif "txt" in character_materials_set[hash][1]:
                for n, file in enumerate(character_materials_set[hash]):
                    if n == 0:
                        continue
                    second_half = f'-{file.split("=", 1)[1].split("-", 1)[1]}'
                    shutil.copyfile(os.path.join(folder_path, file), os.path.join(folder_path, "Character", f"{file.split(second_half)[0]}.txt"))
            else:
                continue
    else:
        with open(os.path.join(folder_path, "Character", "There is no character material in your frame dump folder.txt"), "w") as f:
            f.write("Check your frame dump :(")