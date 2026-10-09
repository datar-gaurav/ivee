# Speel's faces

Animated GIFs shown full screen when a reply carries that emotion.
`firmware/build.sh` packs them into the device's assets partition in place of
upstream's emoji.

Three variants per emotion (`happy_01.gif`, `happy_02.gif`, `happy_03.gif`); the
server picks one at random for each reply (`get_emotion` in
`server/providers/sparky_patches.py`, `FACE_VARIANTS`). `neutral.gif` is the only
single face: the firmware itself sets "neutral" for idle and listening, so the
server can't pick a variant there.

Three faces aren't emotions, and the server picks them itself (same file):
`hi` for the reply to the wake word, `bye` while Speel says goodbye, and
`talking` from the 2nd sentence on in replies of 3+ sentences.

Spec: 420×420 (shown centered on the 480×480 screen), pure black background,
art filling about 404 px, 12 fps, 2 s seamless loop, at most 256 colors.
Flat colors only; soft gradients band. Blinks close over 2+ frames and reopen
over 2+ frames, or they look jerky at 12 fps.

Why not 480×480: the GIF decoder needs 5 bytes per pixel in one LVGL allocation,
and LVGL's allocator caps a single block at 1 MB (`CONFIG_LV_MEM_SIZE_KILOBYTES=1024`).
480×480 needs 1.15 MB and fails silently ("Failed to open GIF"); 420×420 needs about
900 KB. Don't go bigger. All faces together must fit the 8 MB assets
partition alongside fonts and wake-word models (the 70 faces are about 1.9 MB;
the assets image is about 7.3 MB of 8 MB).
The device plays them straight from flash, so more faces cost no RAM.

Upstream has 21 emotion names (`EMOJI_MAP` in upstream `core/utils/textUtils.py`);
all 21 are drawn here, plus hi, bye and talking. `aliases.txt` maps each plain name (`happy`) to its `_01`
variant, as a fallback if the server ever sends the plain name. Aliases cost no space.

To change a face, replace its GIF (same name) and rebuild. To change the number
of variants, keep it the same for every emotion and update `FACE_VARIANTS`.

Why 12 fps, not 24: decoding a 420×420 GIF at 24 fps starved audio playback on
the ESP32-S3 (the voice came out in choppy ~0.3 s chunks with clicks; a static
face made it smooth). Don't raise the frame rate or size without listening for choppy audio.
