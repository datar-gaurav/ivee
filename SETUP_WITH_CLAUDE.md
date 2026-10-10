# Setting up Ivee with Claude Code

This is a playbook for Claude Code (or another coding agent) helping someone set up Ivee. People can read it too; [README.md](README.md) is the short version.

To use it, paste this into Claude Code:

```
Set up Ivee for me: follow https://github.com/datar-gaurav/ivee/blob/main/SETUP_WITH_CLAUDE.md
```

---

## Instructions for the agent

You're helping a parent set up Ivee: a voice companion for a young child, made of an ESP32-S3 device (Waveshare ESP32-S3-Touch-AMOLED-2.16) and a server on their home network. Work through the steps below in order, one at a time, and confirm each one worked before moving on. Explain what you're doing in plain words, because the person may not be technical.

### Ground rules
- **Never ask for API keys in the chat.** The Groq API key goes into the config page (step 3), which never shows it back. If someone pastes a key into the chat anyway, use it only where the config page would, don't repeat it, and suggest they rotate it.
- **You can't type a sudo password.** For commands that need sudo, ask the person to run them with the `!` prefix (for example `! sudo ./server/install.sh`), so the output still reaches you.
- **Ask before anything destructive:** erasing or re-flashing the device, deleting `/opt/ivee` or `~/.ivee`, or changing router settings.
- **Keep the server on the home network.** Never forward ports, open firewalls to the internet or deploy Ivee publicly; the device protocol has no authentication. See [SECURITY.md](SECURITY.md).
- **Don't change the safety rules** in `persona/base-prompt-template.txt`. The character can be changed on the config page.

### 1. Decide where the server runs
Ask where the server should run, and check that machine:
- **A Linux box that stays on (recommended):** Debian, Ubuntu or Raspberry Pi OS, 64-bit (`uname -m` is `x86_64` or `aarch64`), 2 GB of RAM or more, about 6 GB free disk. A Raspberry Pi 4 or 5, or an old mini PC, is ideal.
- **A Mac (Apple Silicon, with Homebrew):** good for trying it out. It only works while the Mac is awake and the script is running.
- **Another machine over SSH:** run the same steps there with `ssh`. Sudo steps need `! ssh -t user@host '…'`.

The device and the server must be on the same Wi-Fi network: the same router, not a separate guest network that blocks devices from seeing each other. The device only supports 2.4 GHz Wi-Fi.

Also ask whether they have a [Groq](https://console.groq.com/keys) account (free) and the device with a USB-C **data** cable. If they don't have a Groq account yet, they can create one while the install runs.

### 2. Install the server
**Linux.** Ask the person to run:
```
! curl -fsSL https://raw.githubusercontent.com/datar-gaurav/ivee/main/install.sh | sudo bash
```
It takes 10–20 minutes. At the end it prints the config page address and a **password that's shown once**; make sure they note it. If it fails, read the error and see [Known problems](#known-problems). Re-running it is safe.

**Mac.** Clone the repo, then run the script in the background with its output in a log file, so you can read it:
```
git clone https://github.com/datar-gaurav/ivee.git ~/ivee && cd ~/ivee
./server/run-mac.sh > ~/.ivee-run.log 2>&1 &
```
Watch `~/.ivee-run.log` until it prints `Config page` (the first run downloads about 2 GB). Tell the person to read the password themselves with `! grep Password ~/.ivee-run.log`. If macOS asks whether Python may accept incoming connections, they must click **Allow**.

### 3. Finish on the config page
Have the person open `http://<server-ip>:8088` in their browser and sign in. On the page they should:
1. Paste the Groq API key and press **Test key**. It should say the key works.
2. Check that **This server's address** matches the detected address.
3. Press **Save and apply**. On Linux the server starts within a few seconds. On a Mac, restart `run-mac.sh` (stop the background job and start it again).

Once it's running, `ivee.local` should resolve to the server: run `dscacheutil -q host -a name ivee.local` on a Mac, or `avahi-resolve -n ivee.local` on Linux.

### 4. Flash the device
Send the person to **https://datar-gaurav.github.io/ivee/** in Chrome or Edge on a desktop computer:
1. Plug in the device and press **Install**. In the port list, pick **USB JTAG/serial debug unit**.
2. Say yes to erasing the device.
3. When it restarts, join the `Ivee-XXXX` Wi-Fi hotspot from a phone or computer. On the setup page, pick the home Wi-Fi, enter its password and press Connect.

The device then finds the server as `ivee.local` by itself; nothing else needs typing.

### 5. Check it works
- In the server log, look for `OTA请求设备ID` (upstream logs in Chinese; it means "OTA request, device ID"). That's the device checking in. Linux: `journalctl -u ivee-server -n 50`. Mac: `~/.ivee-run.log`.
- Ask the person to say **"Speel"** and then a question. The log should show the speech being recognized and a reply.

If both work, you're done. Point them to the config page to change the character or the voice.

---

## Troubleshooting with the device's serial log
When the device shows an error, its USB serial log usually says why. With the device plugged into this computer, read 30 seconds of it. This resets the board so the log starts from boot:
```
python3 -m venv /tmp/ivee-serial && /tmp/ivee-serial/bin/pip install -q pyserial
/tmp/ivee-serial/bin/python - <<'EOF'
import glob, serial, time
port = (glob.glob("/dev/cu.usbmodem*") + glob.glob("/dev/ttyACM*"))[0]
s = serial.Serial(port, 115200, timeout=0.5)
s.dtr = False; s.rts = True; time.sleep(0.1); s.rts = False
end = time.time() + 30
while time.time() < end:
    try:
        print(s.read(4096).decode(errors="replace"), end="", flush=True)
    except serial.SerialException:  # the board's USB port blips while it resets
        time.sleep(0.2)
EOF
```
Close the browser installer tab first, because only one program can use the port at a time. Look for lines starting with `E (` or `W (` after `Network connected`.

## Known problems
| What you see | Cause | Fix |
|---|---|---|
| Serial log: `Ota: … DNS resolution failed` | The device can't resolve `ivee.local`. Either the server isn't publishing it, or the network blocks multicast (guest Wi-Fi, some mesh routers) | Check that `ivee.local` resolves from another computer (step 3). If it does but the device still fails, give the device the address directly: unplug it, plug it back in and press **BOOT** within 3 seconds to reopen the setup hotspot. On the **Advanced** tab set **Custom OTA URL** to `http://<server-ip>:8003/xiaozhi/ota/`, press that tab's Save and wait for "Configuration saved". |
| Serial log: Wi-Fi `auth` / `assoc` failures, never `Got IP` | Wrong password, or a 5 GHz-only network | Reopen setup (BOOT within 3 s) and pick a 2.4 GHz network |
| The server log shows the OTA request, but nothing happens after "Speel" | The WebSocket port is blocked (Mac firewall, or another program on the port) | Allow Python in macOS's firewall prompt. Check `SERVER_WS_PORT` on the config page. |
| `Illegal instruction` during the Linux install | The CPU is too old for the PyTorch build | See the open item in [CLAUDE.md](CLAUDE.md) |
| Config page lists problems | Missing or invalid settings | Fix what it lists, then Save again |
| Speel says it's sleepy and hangs up | Groq's free daily quota ran out | It resets daily. Optionally add an extra provider or a local Ollama model on the config page (Brain section). |
| `Port … is already in use` (Mac) | A previous run is still going, or another app uses the port | Stop the old run, or pick other ports on the config page |
| Lost the config page password | | Linux: `! sudo /opt/ivee/repo/server/install.sh --reset-password`. Mac: delete `~/.ivee/config/ui-password` and restart `run-mac.sh`. |

If you get stuck, collect the server log and the serial log, and help the person open an issue at https://github.com/datar-gaurav/ivee/issues. Leave out the API keys and the Wi-Fi password.
