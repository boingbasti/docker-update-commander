# 🐳 Docker Update Commander

A lightweight, self-hosted web dashboard to check and update your Docker containers. 
It provides a safe, modern, and user-friendly interface to manage container updates manually or automatically.

> **Note:** This tool acts as a control center. It uses the industry-standard **[Watchtower](https://github.com/containrrr/watchtower)** engine internally to perform the actual container replacements safely and reliably.

---

## ✨ Features

### 🖥️ Dashboard & UI
* **High Contrast Dark Mode:** Optimized for readability.
* **Real-time Feedback:** See exactly what's happening (Checking, Updating, Done).
* **Sort & Control:** Sort by name or update status. **Stop** running checks anytime.
* **Local Image Detection:** Smartly detects local-only images to prevent errors.

### ⚡ Performance & Architecture
* **Ultra-Lightweight:** Built on **Alpine Linux** (~68MB image size).
* **Production Ready:** Runs on a robust **Gunicorn** WSGI server.
* **Healthcheck:** Integrated Docker Healthcheck for maximum reliability.
* **Self-Protection:** The dashboard hides itself to prevent accidental self-updates that would freeze the UI.

### 🔄 Update Automation
* **Manual Check:** Check individual containers on demand.
* **Update All:** Update *all* outdated containers with a single click.
* **Background Schedule:** Set automatic check intervals (e.g., every 60 minutes).
* **Selective Auto-Update:** Mark specific containers to be updated automatically in the background ("Set and Forget").

---

## 🚀 Installation (Docker Compose)

The easiest way to run the Update Commander is using Docker Compose.

1. Create a file named `docker-compose.yml`:

```yaml
version: "3.8"
services:
  update-commander:
    image: boingbasti/update-commander:latest
    container_name: update-commander
    restart: unless-stopped
    ports:
      # Standard port is 5000 inside container
      # Mapped to 5005 on host to avoid conflicts
      - "5005:5000"
    volumes:
      # REQUIRED: Access to Docker Socket to control/check containers
      - /var/run/docker.sock:/var/run/docker.sock
      
      # RECOMMENDED: Persist settings (Auto-Update config, intervals, etc.)
      - ./config:/app/config
    environment:
      # Optional: Set your timezone for correct log timestamps
      - TZ=Europe/Berlin
```

2. Start the container:

```bash
docker compose up -d
```

3. Access the dashboard at: `http://<your-server-ip>:5005`

---

## ⚙️ Configuration

All settings can be managed directly via the **Settings (⚙️)** button in the web interface. 
**No environment variables needed!**

* **Check Strategy:** Choose between *Manual Only*, *On Page Load*, or *Background Schedule*.
* **Interval:** Set the background check frequency (in minutes).
* **Auto-Update:** Enable auto-updates for *All* or *Selected Containers* (only available in Background mode).

*Settings are saved persistently to `/app/config/config.json`.*

---

## 🛡️ How it works

1.  **The Brain (This Tool):** The Python/Flask application connects to the Docker Socket to list containers and check for new image hashes on the registry (Docker Hub, GHCR, etc.).
2.  **The Muscle (Watchtower):** When you trigger an update, this tool spawns a temporary, one-off `containrrr/watchtower` container. 
3.  **The Execution:** Watchtower pulls the new image, stops the old container, and recreates it with **exact** configuration (Ports, ENVs, Volumes) as before.

## ⚠️ Limitations

* **Self-Update:** To update the *Update Commander* itself, please use Portainer or the CLI (`docker compose pull && docker compose up -d`). The internal update mechanism is disabled for this container to ensure the web interface doesn't crash during the process.
* **Docker Compose Changes:** This tool updates the *Image Version*. If you change your `docker-compose.yml` structure (e.g., adding a new volume), you must redeploy the stack manually.

## 🤝 Credits

* **Updater Engine:** [Watchtower](https://github.com/containrrr/watchtower) by containrrr.
* **Icons:** Bootstrap Icons.

---

*Created with ❤️ by boingbasti*
