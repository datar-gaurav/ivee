# Ivee

A small, kid-safe voice companion with an animated face, for about $0 a month.

A child says **"Speel"**, and Speel, a friendly little robot on an AMOLED screen, wakes up, listens and answers in a gentle voice, with an animated face that matches the feeling. Speel plays guessing games, counts, tells tiny stories and always steers back to safe topics.

```
┌──────────────────────┐   WebSocket + Opus   ┌───────────────────────────────────┐
│ ESP32-S3 AMOLED 2.16 │ ───────────────────▶ │ Ivee server (any small Linux box) │
│ wake word, mic, AEC, │ ◀─────────────────── │  ├─ Speech → text: Groq Whisper   │
│ speaker, 70 faces    │                      │  ├─ Replies: Groq or local Ollama │
└──────────────────────┘                      │  ├─ Voice: Kokoro, on the server  │
                                              │  └─ Config page  :8088            │
                                              └───────────────────────────────────┘
```

## What you need
- **The device:** a [Waveshare ESP32-S3-Touch-AMOLED-2.16](https://www.waveshare.com/esp32-s3-touch-amoled-2.16.htm) and a USB-C data cable.
- **A server on your home network that stays on:** any 64-bit machine running Debian, Ubuntu or Raspberry Pi OS with 2 GB of RAM or more (an old mini PC, a Raspberry Pi 4 or 5). A Mac works for trying it out.
- **A free [Groq](https://console.groq.com/keys) API key.**
- A computer with Chrome or Edge, to flash the device once.

## Quick start

**1. Install the server.** On the server:
```bash
curl -fsSL https://raw.githubusercontent.com/datar-gaurav/ivee/main/install.sh | sudo bash
```
It takes 10–20 minutes and ends by printing the config page's address and password.

**2. Finish setup on the config page.** Open `http://<server-ip>:8088`, sign in, paste your Groq API key and press **Save and apply**. The page also shows the device URL you'll need next.

**3. Flash the device.** Open the [web installer](https://datar-gaurav.github.io/ivee/) in Chrome or Edge, plug in the device and press Install. When it restarts, join its `Ivee-XXXX` Wi-Fi hotspot. On the setup page's **Advanced** tab, set **Custom OTA URL** to the address from the config page, then connect it to your Wi-Fi.

**4. Say "Speel".**

## Kid safety and privacy
- **The safety rules always apply.** They live in [`persona/base-prompt-template.txt`](persona/base-prompt-template.txt) and the config page can't change them: age-appropriate topics only, no personal information, no secrets from parents, send the child to a grown-up when hurt or scared, no instructions for anything dangerous, and no pretending to be a person. Replies are short and in simple English.
- **It is not a babysitter.** A language model can still say something wrong or odd. Try it yourself first, and stay nearby.
- **What leaves your home:** Groq receives the recording of each thing the child says (for speech-to-text) and the text of the conversation (for replies, unless a local Ollama model answers). If you add an extra provider, it receives the conversation text too. The voice, the faces and the settings stay on your network. Ivee keeps no long-term memory of the child, and the server deletes recordings after transcribing them. Read Groq's privacy policy before you decide.
- **Keep the server on your home network.** The config page is password-protected but meant for your LAN only, and the device protocol has no authentication. See [SECURITY.md](SECURITY.md).

## Free-tier budget
Groq's free plan allows roughly 1,000 requests and 200K tokens a day per model. At about 1.3K tokens a reply, that's around 150 replies a day on the main model and as many again on the fallback model. When both run out, Speel says it's sleepy and hangs up until the quota resets. A local [Ollama](https://ollama.com) model (set on the config page) removes that limit for replies.

## Customizing
On the config page (`http://<server-ip>:8088`):
- **Character:** who Speel is and how it talks. The safety rules stay separate.
- **Voice:** any [Kokoro](https://huggingface.co/hexgrad/Kokoro-82M) voice, with speed, pitch and small-speaker EQ. **Play** previews it.
- **Brain:** which Groq models answer, a local Ollama model, or an **extra provider**: any OpenAI-compatible API (NVIDIA NIM, OpenRouter, a paid provider) tried before Groq, with Groq as the fallback. Some free tiers are for testing and evaluation only, so check the provider's terms.

Changing the **faces** or the **wake word** means building the firmware yourself: see [firmware/README.md](firmware/README.md) and [firmware/faces/README.md](firmware/faces/README.md).

## Running it on a Mac
To try it without a Linux box (Apple Silicon, Homebrew):
```bash
git clone https://github.com/datar-gaurav/ivee.git && cd ivee
./server/run-mac.sh
```
It runs in the foreground; Ctrl-C stops it. It prints the config page's address and password too. Settings live in `~/.ivee/config/ivee.env`.

## Maintenance
- **Update:** re-run the install command, or `cd /opt/ivee/repo && sudo git pull && sudo ./server/install.sh`.
- **Logs:** `journalctl -u ivee-server -f` (also `ivee-tts` and `ivee-config`).
- **Lost the config page password:** `sudo /opt/ivee/repo/server/install.sh --reset-password`.
- **Backups:** set a backup folder on the config page, then add `30 2 * * * /opt/ivee/repo/server/scripts/backup.sh` to root's crontab.

## Troubleshooting
- **The device can't reach the server:** check the Custom OTA URL you entered on the device (re-flashing with erase brings back the Wi-Fi setup hotspot), and that the server's address hasn't changed. Reserve the address on your router.
- **`Illegal instruction` during install:** the CPU is too old for the PyTorch build. See the open item in [CLAUDE.md](CLAUDE.md).
- **Speel is sleepy all the time:** the Groq free quota ran out. It resets daily.
- **Choppy voice:** read [firmware/faces/README.md](firmware/faces/README.md) before adding bigger or faster faces.

## How it's built
Ivee is a thin layer on two open-source projects and doesn't fork either one: [xiaozhi-esp32](https://github.com/78/xiaozhi-esp32) (device firmware) and [xiaozhi-esp32-server](https://github.com/xinnan-tech/xiaozhi-esp32-server) (voice server). The installer checks the server out at a pinned commit, adds Ivee's providers as new files and configures it with an overlay. The firmware gets a few small patches and Speel's faces. [docs/decisions.md](docs/decisions.md) explains why.

| Path | What it is |
|---|---|
| `persona/` | The character and the safety rules |
| `install.sh`, `server/install.sh` | The one-line bootstrap, and the Linux installer |
| `server/config_ui/` | The config page (Python standard library only) |
| `server/providers/` | Ivee's additions to the upstream server: Groq tuning, faces, audio fixes |
| `server/tts/` | Speel's voice: Kokoro behind a small local HTTP API |
| `firmware/` | Firmware build script, patches and faces |
| `docs/flash/` | The browser installer page |

## Contributing
Issues and pull requests are welcome; see [CONTRIBUTING.md](CONTRIBUTING.md).

## License
MIT, including the face art: see [LICENSE](LICENSE). Third-party parts keep their own licenses: see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
