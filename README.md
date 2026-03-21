# 🐳 Docker Update Commander

A lightweight, self-hosted web dashboard to check and update your Docker containers.
It provides a safe, modern, and user-friendly interface to manage container updates manually or automatically.

> **Note:** This tool acts as a control center. It uses the industry-standard **[Watchtower](https://github.com/containrrr/watchtower)** engine internally to perform the actual container replacements safely and reliably.

---

## ✨ Core Features

* **Web Dashboard:** Clean, high-contrast Dark Mode interface.
* **Smart Automation:** Background checks and selective auto-updates ("Set and Forget").
* **Real-time Feedback:** See exactly what's happening (Checking, Updating, Done).
* **Flexible Updates:** Update all containers at once, or use Select Mode to pick specific ones.
* **Self-Update Support:** The dashboard can update itself — it triggers last so all other containers finish first.
* **Cleanup Option:** Automatically remove replaced images after an update to keep your host clean.
* **Production Ready:** Built on **Alpine Linux** (~68MB) running on a robust **Gunicorn** WSGI server.
* **Local Image Support:** Smartly detects local-only images to prevent update errors.
* **Sort & Control:** Sort containers by name or update status. **Stop** running checks anytime.

---

## 🚀 Quick Start (Docker Compose)

The easiest way to run the Update Commander.

```yaml
services:
  update-commander:
    image: boingbasti/update-commander:latest
    container_name: update-commander
    restart: unless-stopped
    ports:
      - "5005:5000"
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
      - ./config:/app/config
    environment:
      - TZ=Europe/Berlin
```

---

## ⚙️ Configuration & Volumes

No complex environment variables needed! All logic settings are managed via the Web UI.

| Type | Path / Variable | Description | Notes |
|------|----------------|-------------|-------|
| **Volume** | `/var/run/docker.sock` | Docker Socket | **Required** to control containers |
| **Volume** | `/app/config` | Config Storage | Stores `config.json` (Settings) |
| **Port** | `5000` | Web Interface | Map to host (e.g. `5005:5000`) |
| **Env** | `TZ` | Timezone | Optional (e.g. `Europe/Berlin`) |

### UI Settings (accessible via ⚙️ button)

* **Check Strategy:** Choose between *Manual Only*, *On Page Load*, or *Background Schedule*.
* **Interval:** Set the background check frequency (in minutes).
* **Cleanup:** Optionally remove the old image after each update. Images still used by other containers are kept automatically.
* **Auto-Update:** Enable auto-updates for *All* or *Selected Containers* (only available in Background mode).

---

## 🛡️ How it works

1.  **The Brain (This Tool):** The Python/Flask application connects to the Docker Socket to list containers and check for new image hashes on the registry (Docker Hub, GHCR, etc.).
2.  **The Muscle (Watchtower):** When you trigger an update, this tool spawns a temporary, one-off `containrrr/watchtower` container.
3.  **The Execution:** Watchtower pulls the new image, stops the old container, and recreates it with **exact** configuration (Ports, ENVs, Volumes) as before.

---

## ⚠️ Limitations

* **Docker Compose Changes:** This tool updates the *Image Version*. If you change your `docker-compose.yml` structure (e.g., adding a new volume), you must redeploy the stack manually.

---

## 📎 Links

Docker Hub: https://hub.docker.com/r/boingbasti/update-commander
GitHub: https://github.com/boingbasti/docker-update-commander

---
*Created with ❤️ by boingbasti*
