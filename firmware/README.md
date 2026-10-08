# Firmware: Waveshare ESP32-S3-Touch-AMOLED-2.16

Use upstream [78/xiaozhi-esp32](https://github.com/78/xiaozhi-esp32). It includes this board at `main/boards/waveshare/esp32-s3-touch-amoled-2.16` (with device AEC enabled). Checked against firmware v2.5.1.

## Prereqs
- ESP-IDF **v6.0.1 or newer** (v6.1 recommended). 5.x is no longer supported.
- A USB-C data cable.

## Build
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
