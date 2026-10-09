# Third-party notices

Ivee's own code and face art are MIT-licensed (see [LICENSE](LICENSE)). Ivee builds
on the projects below, which keep their own licenses. This repository doesn't
contain their code; the installer and the firmware build download them.

## Server (downloaded by the installer)
| Project | License | Used for |
|---|---|---|
| [xiaozhi-esp32-server](https://github.com/xinnan-tech/xiaozhi-esp32-server), © 2025 xinnan-tech | MIT | The voice server (checked out as `ivee-esp32-server`) |
| [Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M) model | Apache-2.0 | Speel's voice |
| [kokoro-onnx](https://github.com/thewh1teagle/kokoro-onnx) | MIT | Running the Kokoro model |
| [piper-tts](https://github.com/OHF-Voice/piper1-gpl) (with espeak-ng) | GPL-3.0-or-later | Turning text into phonemes for Kokoro. Installed as a separate package and used unmodified |
| [Silero VAD](https://github.com/snakers4/silero-vad) | MIT | Detecting when the child stops talking (upstream server) |
| PyTorch, NumPy and the upstream server's other Python dependencies | Their own licenses | See the upstream server's `requirements.txt` |

## Firmware (in the prebuilt release binaries)
| Project | License |
|---|---|
| [xiaozhi-esp32](https://github.com/78/xiaozhi-esp32), © 2025 Shenzhen Xinzhi Future Technology Co., Ltd. and project contributors | MIT |
| [ESP-IDF](https://github.com/espressif/esp-idf) | Apache-2.0 |
| [ESP-SR](https://github.com/espressif/esp-sr) (wake word) and other Espressif components | Espressif MIT (use on Espressif chips) or Apache-2.0 |
| [LVGL](https://github.com/lvgl/lvgl) and other components | MIT and others |

Each firmware release includes `firmware-licenses.zip` with the full license
text of every component built into it.

## Browser installer
| Project | License |
|---|---|
| [ESP Web Tools](https://github.com/esphome/esp-web-tools) | Apache-2.0 |
