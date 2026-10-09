# Security

## Reporting a problem
Please report security or child-safety problems privately via GitHub's **Report a vulnerability** (Security tab) rather than in a public issue. That includes a way to get Speel past its safety rules. I'll reply within a week.

## How Ivee is meant to run
- **On your home network only.** Don't forward ports to the server. The device protocol (WebSocket on 8000, OTA on 8003) has no authentication; anyone who can reach it can talk to Speel and use your Groq quota. Upstream's `server.auth` is off by default (see the config template).
- **The config page (port 8088)** is password-protected: the password is stored as a salted PBKDF2 hash, sessions use `HttpOnly`, `SameSite=Strict` cookies, and requests from other sites are refused. It's plain HTTP, so anyone on your network who can see your traffic could read the password and the settings. It never sends the Groq API key back to the browser.
- **Privileges.** The services run as the unprivileged `ivee` user. Saving on the config page writes `/opt/ivee/config/apply.request`; a root `ivee-apply` unit then renders the config as `ivee` and restarts the services. Root only restarts services; it never writes into directories `ivee` controls. The repository in `/opt/ivee/repo` stays owned by root.
- **Secrets.** `/opt/ivee/config/ivee.env` and the rendered `.config.yaml` hold your Groq API key and are readable only by `ivee`.
