# Remote control from iPhone (Remote API + Taildrop)

Control OmniDL from an iPhone, and send finished downloads straight to it, over Tailscale. The two features work independently but pair well together.

**Requirements**

- Tailscale installed on both computer and iPhone — [tailscale.com](https://tailscale.com)
- Both devices signed into the same Tailscale account (same tailnet)
- Remote API extra installed: `uv sync --extra api --group dev`

## 1. Connect both devices with Tailscale

Install Tailscale on both devices, sign in with the same account, and confirm the computer shows up in the iPhone's Tailscale app. Note the computer's MagicDNS name (Tailscale's automatic hostname for a device, e.g. `my-laptop`) — it stays stable even if the IP changes.

## 2. Enable the Remote API in OmniDL

Open **Settings → Remote API** and turn it on. OmniDL generates a random API token and stores it in the system keyring (Windows Credential Manager / macOS Keychain, never in a config file). The server starts on `http://0.0.0.0:8765`, listening on all interfaces including Tailscale.

Every request needs the `X-API-Token` header. Don't expose port 8765 to the public internet without Tailscale.

## 3. Open the app on iPhone

In Safari, go to `http://<computer-name>:8765` (e.g. `http://my-laptop:8765`) and enter the API token when asked. Use Share → "Add to Home Screen" to install it like a native app.

If it won't connect: confirm Tailscale is on for both devices, or try the Tailscale IP instead of the name (e.g. `http://100.64.0.5:8765`).

| Feature | What it does |
|---|---|
| Paste URL + Analyse | Analyze a link, pick format/quality |
| Download | Start a download, watch progress live |
| Queue | View all tasks, pause/resume/cancel |
| Convert | Convert a downloaded file to an iPhone-friendly MP4 |
| Preview | View video/image in-browser |
| Send (Taildrop) | Send the file to the iPhone over Tailscale |
| Delete | Remove the file from the computer |

## 4. Set up Taildrop to receive files

Taildrop sends files peer-to-peer between Tailscale devices — no cloud involved.

In **Settings → Taildrop**, enable it, click **Detect nodes** to find online Tailscale devices, and check your iPhone in the list (you can select more than one). Choose a send mode:

- **Ask** (default) — files are sent only when you press "Send" manually
- **Always** — every finished download is sent automatically

On the iPhone, a Taildrop notification appears for each incoming file; tap it, choose **Save**, and it lands in the Files app. File names with emoji or special characters are sanitized automatically before sending.

You can also trigger a manual send from the desktop Queue tab (**Send** button) or from the Remote App queue.

## Typical flow

```
iPhone (Safari/PWA)                     Computer (OmniDL)
──────────────────────────────────────────────────────────
1. Open Remote App, paste a TikTok link
2. Tap Analyse                     →   Analyzes the URL
3. Pick MP4, tap Download          →   Starts downloading
4. Watch live progress             ←   SSE events
5. Tap Convert                     →   FFmpeg converts to iPhone MP4
6. Tap Send                        →   Tailscale sends the file
7. Notification appears            ←   File arrives
8. Tap Save in Files app
9. Tap Delete in Remote App        →   File removed from the computer
```

## Start automatically on Windows login

Create a shortcut to `OmniDL.exe`, press `Win + R`, type `shell:startup`, and drop the shortcut into that folder. OmniDL will launch with Windows and the Remote API / Taildrop will be ready if they were enabled before.
