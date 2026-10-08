# Speel's faces

One animated GIF per emotion, shown full screen when a reply carries that emotion.
`firmware/build.sh` packs them into the device's assets partition in place of
upstream's emoji.

Spec: 360×360 (shown centered on the 480×480 screen), pure black background,
24 fps, 2 s seamless loop, under 256 colors. Flat colors only; soft gradients band.

Why not 480×480: the GIF decoder needs 5 bytes per pixel in one LVGL allocation,
and LVGL's allocator caps a single block at 1 MB (`CONFIG_LV_MEM_SIZE_KILOBYTES=1024`).
480×480 needs 1.15 MB and fails silently ("Failed to open GIF"); 360×360 needs 648 KB.
These were cropped losslessly from 480×480 masters whose art sat inside x 68–412, y 82–412.

The server sends 21 emotion names (`EMOJI_MAP` in upstream `core/utils/textUtils.py`).
The 10 drawn here cover them; `aliases.txt` maps the rest to the closest face.
`neutral` doubles as the idle and listening face.

To change a face, replace its GIF (same name) and rebuild.
