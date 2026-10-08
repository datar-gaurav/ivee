# Firmware: Waveshare ESP32-S3-Touch-AMOLED-2.16

Use upstream [78/xiaozhi-esp32](https://github.com/78/xiaozhi-esp32). It includes this board at `main/boards/waveshare/esp32-s3-touch-amoled-2.16` (with device AEC enabled). Checked against firmware v2.5.1.

## Prereqs
- ESP-IDF **v6.0.1 or newer** (v6.1 recommended). 5.x is no longer supported.
- A USB-C data cable.

## Quick path: `firmware/build.sh`
```bash
./firmware/build.sh --list-wake-words          # pre-trained WakeNet models
./firmware/build.sh "hi speel"                 # or a model name, e.g. wn9_hijolly_tts2
./firmware/build.sh --flash                    # flash + serial monitor
```
It pins the firmware commit, sets the OTA URL from `server/.env` (no menuconfig) and checks that the URL made it into `sdkconfig`. It also applies `firmware/patches/*.patch` to the checkout (currently one: always use the dark theme, so the black around Speel's face isn't white) and packs `firmware/faces/` into the assets partition. A phrase that isn't a model name uses MultiNet7 English with on-device phonemes. It needs no training, but it's less accurate than a trained WakeNet model, so test it with the child's voice.

Known upstream quirk: the board's `config.json` sets `CONFIG_USE_DEVICE_AEC=y`, but the firmware Kconfig doesn't list this board for device AEC, so it's dropped. Result: half-duplex (the device doesn't listen while Sparky talks, so no barge-in). Fine for now.

## Manual build
```bash
git clone https://github.com/78/xiaozhi-esp32.git && cd xiaozhi-esp32
. $IDF_PATH/export.sh

python scripts/build.py --list-boards | grep amoled-2.16     # confirm the board id
python scripts/build.py --list-wake-words                    # pick an English wake word model
```

**Point the device at Sparky's server, not the default cloud.** Run `idf.py menuconfig`, go to **Xiaozhi Assistant → Default OTA URL**, and set it to:
```
http://<SERVER_LAN_IP>:8003/xiaozhi/ota/
```

Then build in English with your chosen wake word:
```bash
python scripts/build.py waveshare/esp32-s3-touch-amoled-2.16 --language en-US --wake-word <model>
```

## Flash
```bash
idf.py -p /dev/cu.usbmodem* flash monitor
```
On first boot the device opens a Wi-Fi setup hotspot. Join it from a phone and enter your home Wi-Fi.

## Notes
- The OTA URL can also be stored on the device (NVS key `ota_url` in the `wifi` namespace), which overrides the compiled default.
- The face shows the emoji that the LLM puts at the start of each reply (see `persona/base-prompt-template.txt`).
- To check that it hit your server and not the cloud, watch `journalctl -u sparky-server -f` while the device boots.
