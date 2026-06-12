from flask import Flask, render_template, jsonify, request
import docker
import socket
import logging
import json
import os
import time
import threading
from datetime import datetime

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)

# --- Configuration Setup ---
CONFIG_DIR = "/app/config"
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")

os.makedirs(CONFIG_DIR, exist_ok=True)

# Global Cache
SERVER_CACHE = {}
CACHE_LOCK = threading.Lock()

# Default settings
DEFAULT_CONFIG = {
    "check_mode": "manual",       # Options: 'manual', 'startup', 'background'
    "check_interval": 60,         # Minutes
    "auto_update_mode": "off",    # Options: 'off', 'all', 'selected'
    "auto_update_containers": [], # List of container names
    "remove_old_images": False,   # Remove replaced images after update
    "restart_dependents": False   # Restart containers that share network with updated container
}

# Docker Setup
try:
    client = docker.from_env()
except Exception as e:
    logger.error(f"Failed to connect to Docker Socket: {e}")
    client = None

UPDATER_IMAGE = "containrrr/watchtower"

# --- Helper Functions ---

def load_config():
    """Loads configuration from JSON file or returns defaults."""
    if not os.path.exists(CONFIG_FILE):
        return DEFAULT_CONFIG.copy()
    try:
        with open(CONFIG_FILE, 'r') as f:
            return {**DEFAULT_CONFIG, **json.load(f)}
    except Exception as e:
        logger.error(f"Error loading config: {e}")
        return DEFAULT_CONFIG.copy()

def save_config(config):
    """Saves configuration to JSON file."""
    try:
        with open(CONFIG_FILE, 'w') as f:
            json.dump(config, f, indent=4)
        return True
    except Exception as e:
        logger.error(f"Error saving config: {e}")
        return False

def get_image_name(container):
    """Robustly retrieve the image name."""
    try:
        if container.image.tags:
            return container.image.tags[0]
        image_config = container.attrs.get('Config', {}).get('Image', '')
        if image_config:
            return image_config
        return "unknown-image"
    except Exception as e:
        return "unknown"

def perform_single_check(container_id):
    """
    Performs the update check logic for a single container.
    """
    container = client.containers.get(container_id)
    current_img = container.image
    current_id = current_img.id
    
    image_name = get_image_name(container)
    
    if not image_name or image_name == "unknown-image":
        raise ValueError("Could not determine image name")

    current_created = current_img.attrs.get('Created', 'Unknown')
    
    new_id = None
    new_created = None
    update_available = False
    is_local = False

    # Try to pull latest image
    try:
        new_img = client.images.pull(image_name)
        new_id = new_img.id
        new_created = new_img.attrs.get('Created', 'Unknown')
        update_available = new_id != current_id
        
    except (docker.errors.NotFound, docker.errors.APIError):
        is_local = True
        logger.info(f"Local detection: '{image_name}' not found on registry. Treating as local image.")
    except Exception as e:
        raise e

    result = {
        'update_available': update_available,
        'is_local': is_local,
        'current_id_short': current_id.split(':')[-1][:12],
        'new_id_short': new_id.split(':')[-1][:12] if new_id else "n/a",
        'current_created': current_created,
        'new_created': new_created if new_created else "n/a",
        'checked_at': datetime.now().isoformat()
    }
    
    with CACHE_LOCK:
        SERVER_CACHE[container_id] = result
        
    return result

def trigger_updater_engine(container_name, old_image_id=None):
    """Triggers the external updater engine (Watchtower)."""

    # 1. FIX: Ensure we have the LATEST Watchtower image to avoid old API clients
    try:
        logger.info(f"Pulling latest updater image: {UPDATER_IMAGE}")
        client.images.pull(UPDATER_IMAGE)
    except Exception as e:
        logger.warning(f"Could not pull latest updater image, using local cache: {e}")

    # 2. FIX: Force newer API version via Environment Variable
    client.containers.run(
        image=UPDATER_IMAGE,
        command=f"--run-once {container_name}",
        volumes={'/var/run/docker.sock': {'bind': '/var/run/docker.sock', 'mode': 'rw'}},
        environment={'DOCKER_API_VERSION': '1.44'},  # Solves "client version 1.25 is too old"
        auto_remove=True
    )

    # 3. Remove old image if setting is enabled
    if old_image_id:
        config = load_config()
        if config.get('remove_old_images', False):
            try:
                # Check if any RUNNING container still uses the old image — don't break those.
                # Stopped containers referencing it are irrelevant (force=True handles them).
                running_users = [
                    c.name for c in client.containers.list()
                    if c.image.id == old_image_id
                ]
                if running_users:
                    logger.info(f"Skipping removal of old image {old_image_id[:12]}: still used by running containers {running_users}")
                else:
                    client.images.remove(old_image_id, force=True)
                    logger.info(f"Removed old image {old_image_id[:12]} after update of {container_name}")
            except Exception as e:
                logger.warning(f"Could not remove old image {old_image_id[:12]}: {e}")

def get_dependent_containers(container_id, container_name):
    """Find running containers whose network namespace is shared with the given container."""
    dependents = []
    try:
        for c in client.containers.list():
            if c.id == container_id:
                continue
            network_mode = c.attrs.get('HostConfig', {}).get('NetworkMode', '')
            if network_mode.startswith('container:'):
                ref = network_mode.split('container:', 1)[1]
                # ref can be full id, short id, or name
                if ref == container_name or ref == container_id or container_id.startswith(ref):
                    dependents.append(c)
    except Exception as e:
        logger.warning(f"Could not determine dependent containers: {e}")
    return dependents

def collect_dependents_if_enabled(container_id, container_name):
    """Collect dependent containers BEFORE the update while they are still running."""
    config = load_config()
    if not config.get('restart_dependents', False):
        return []
    return get_dependent_containers(container_id, container_name)

def wait_for_healthy(container_name, timeout=180):
    """Wait until the named container is healthy, mirroring depends_on: condition: service_healthy."""
    logger.info(f"Waiting for '{container_name}' to be healthy before restarting dependents...")
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            matches = client.containers.list(filters={'name': container_name})
            for c in matches:
                if c.name == container_name:
                    c.reload()
                    health = c.attrs.get('State', {}).get('Health', {})
                    if health:
                        if health.get('Status') == 'healthy':
                            logger.info(f"'{container_name}' is healthy, restarting dependents.")
                            return True
                    else:
                        if c.status == 'running':
                            logger.info(f"'{container_name}' is running (no healthcheck), restarting dependents.")
                            return True
                    break
        except Exception as e:
            logger.warning(f"Error checking health of '{container_name}': {e}")
        time.sleep(5)
    logger.warning(f"Timed out waiting for '{container_name}' to become healthy after {timeout}s — restarting dependents anyway.")
    return False

def recreate_with_updated_network(dep_container, updated_container_name):
    """Recreate a container whose NetworkMode references a stale container ID.

    Docker Compose stores network_mode as container:<id>, not container:<name>.
    When the provider is recreated by Watchtower (new ID), Docker can no longer
    join the old namespace. Recreation with the container name instead of ID makes
    all subsequent updates self-healing (start() will suffice from then on).
    """
    dep_name = dep_container.name
    config = dep_container.attrs.get('Config', {})
    hc = dep_container.attrs.get('HostConfig', {})
    new_network_mode = f"container:{updated_container_name}"
    logger.info(f"Recreating '{dep_name}' with network_mode={new_network_mode}")

    try:
        dep_container.remove(force=True)
    except Exception as e:
        logger.warning(f"Could not remove '{dep_name}' before recreation: {e}")
        return False

    try:
        run_kwargs = {
            'image': config.get('Image'),
            'name': dep_name,
            'detach': True,
            'environment': config.get('Env') or [],
            'network_mode': new_network_mode,
            'labels': config.get('Labels') or {},
        }
        if hc.get('Binds'):
            run_kwargs['volumes'] = hc['Binds']
        if hc.get('CapAdd'):
            run_kwargs['cap_add'] = hc['CapAdd']
        if hc.get('CapDrop'):
            run_kwargs['cap_drop'] = hc['CapDrop']
        if hc.get('Privileged'):
            run_kwargs['privileged'] = hc['Privileged']
        if hc.get('Devices'):
            run_kwargs['devices'] = hc['Devices']
        if hc.get('Sysctls'):
            run_kwargs['sysctls'] = hc['Sysctls']
        if hc.get('Tmpfs'):
            run_kwargs['tmpfs'] = hc['Tmpfs']
        if hc.get('RestartPolicy', {}).get('Name'):
            run_kwargs['restart_policy'] = hc['RestartPolicy']
        if config.get('Entrypoint'):
            run_kwargs['entrypoint'] = config['Entrypoint']
        if config.get('Cmd'):
            run_kwargs['command'] = config['Cmd']

        client.containers.run(**run_kwargs)
        logger.info(f"Recreated '{dep_name}' successfully — future updates will use start() directly")
        return True
    except Exception as e:
        logger.error(f"Failed to recreate container '{dep_name}': {e}")
        return False

def restart_collected_dependents(dependents, updated_name):
    """Wait for the updated container to be healthy, then start dependent containers."""
    if not dependents:
        return []
    wait_for_healthy(updated_name)
    restarted = []
    for dep in dependents:
        dep_name = dep.name
        try:
            # Re-fetch by name — the original object may be stale if the container was
            # recreated by Watchtower during a mass update earlier in the same cycle.
            try:
                fresh = client.containers.get(dep_name)
            except docker.errors.NotFound:
                logger.warning(f"Dependent container '{dep_name}' not found after update, skipping")
                continue

            if fresh.status == 'running':
                logger.info(f"Dependent '{dep_name}' is already running, no action needed")
                restarted.append(dep_name)
                continue

            logger.info(f"Starting dependent container '{dep_name}' (status: {fresh.status})")
            try:
                fresh.start()
                restarted.append(dep_name)
                logger.info(f"Started dependent container '{dep_name}' after update of '{updated_name}'")
            except Exception as start_err:
                if 'joining network namespace' in str(start_err) and 'No such container' in str(start_err):
                    # NetworkMode holds the old provider container ID (set by Docker Compose at
                    # creation time). Recreate with the provider name so future updates work too.
                    logger.info(f"'{dep_name}' has a stale network namespace reference — recreating")
                    if recreate_with_updated_network(fresh, updated_name):
                        restarted.append(dep_name)
                else:
                    logger.warning(f"Could not start dependent container '{dep_name}': {start_err}")
        except Exception as e:
            logger.warning(f"Could not handle dependent container '{dep_name}': {e}")
    return restarted

# --- Background Worker ---

def background_worker():
    """
    Runs in a separate thread. Checks config and performs updates if enabled.
    """
    logger.info("Background worker started.")
    last_check_time = 0

    while True:
        try:
            config = load_config()
            mode = config.get('check_mode', 'manual')
            
            if mode == 'background':
                interval_minutes = int(config.get('check_interval', 60))
                interval_seconds = interval_minutes * 60
                
                if (time.time() - last_check_time) >= interval_seconds:
                    logger.info("Background schedule: Starting check cycle...")
                    
                    try:
                        containers = client.containers.list()
                        current_hostname = socket.gethostname()
                        
                        auto_up_mode = config.get('auto_update_mode', 'off')
                        auto_up_list = config.get('auto_update_containers', [])
                        
                        self_container = None
                        for c in containers:
                            image_name = get_image_name(c)
                            if "watchtower" in image_name: continue
                            if c.attrs.get('Config', {}).get('Hostname', '') == current_hostname:
                                self_container = c
                                continue  # process self last

                            try:
                                result = perform_single_check(c.id)

                                if result['update_available'] and not result.get('is_local', False):
                                    should_update = False
                                    if auto_up_mode == 'all':
                                        should_update = True
                                    elif auto_up_mode == 'selected' and c.name in auto_up_list:
                                        should_update = True

                                    if should_update:
                                        logger.info(f"Auto-Update triggered for {c.name}")
                                        saved_id = c.id
                                        dependents = collect_dependents_if_enabled(saved_id, c.name)
                                        trigger_updater_engine(c.name, c.image.id)
                                        restart_collected_dependents(dependents, c.name)
                                        with CACHE_LOCK:
                                            if saved_id in SERVER_CACHE: del SERVER_CACHE[saved_id]

                            except Exception as inner_e:
                                logger.warning(f"Failed to process {c.name}: {inner_e}")

                        # Handle self last so all other containers update first
                        if self_container:
                            try:
                                result = perform_single_check(self_container.id)
                                if result['update_available'] and not result.get('is_local', False):
                                    should_update = False
                                    if auto_up_mode == 'all':
                                        should_update = True
                                    elif auto_up_mode == 'selected' and self_container.name in auto_up_list:
                                        should_update = True
                                    if should_update:
                                        logger.info(f"Auto-Update triggered for self: {self_container.name}")
                                        saved_self_id = self_container.id
                                        dependents = collect_dependents_if_enabled(saved_self_id, self_container.name)
                                        trigger_updater_engine(self_container.name, self_container.image.id)
                                        restart_collected_dependents(dependents, self_container.name)
                            except Exception as inner_e:
                                logger.warning(f"Failed to process self container: {inner_e}")
                        
                        last_check_time = time.time()
                        logger.info("Background cycle finished.")
                        
                    except Exception as e:
                        logger.error(f"Error during container loop: {e}")

            time.sleep(10)
            
        except Exception as outer_e:
            logger.error(f"Critical worker error: {outer_e}")
            time.sleep(60)

worker_thread = threading.Thread(target=background_worker, daemon=True)
worker_thread.start()

# --- Routes ---

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/settings', methods=['GET'])
def get_settings():
    return jsonify(load_config())

@app.route('/api/settings', methods=['POST'])
def update_settings():
    new_settings = request.json
    current_config = load_config()
    allowed_keys = ["check_mode", "check_interval", "auto_update_mode", "auto_update_containers", "remove_old_images", "restart_dependents"]
    for key in allowed_keys:
        if key in new_settings:
            current_config[key] = new_settings[key]
    if save_config(current_config):
        return jsonify({'success': True, 'config': current_config})
    else:
        return jsonify({'error': 'Failed to save config'}), 500

@app.route('/api/containers')
def list_containers():
    if not client: return jsonify({'error': 'Docker socket not connected.'}), 500
    containers = []
    current_hostname = socket.gethostname()

    try:
        all_containers = client.containers.list()
    except Exception as e:
        return jsonify({'error': str(e)}), 500

    for c in all_containers:
        try:
            image_name = get_image_name(c)
            if "watchtower" in image_name: continue
            is_self = c.attrs.get('Config', {}).get('Hostname', '') == current_hostname

            network_mode = c.attrs.get('HostConfig', {}).get('NetworkMode', '')
            depends_on_container = network_mode.split('container:', 1)[1] if network_mode.startswith('container:') else None

            container_data = {
                'id': c.id,
                'name': c.name,
                'image': image_name,
                'status': c.status,
                'short_id': c.short_id,
                'is_self': is_self,
                'depends_on_container': depends_on_container,
                'cached_result': None
            }
            
            with CACHE_LOCK:
                if c.id in SERVER_CACHE:
                    container_data['cached_result'] = SERVER_CACHE[c.id]
            
            containers.append(container_data)
        except Exception as e:
            continue

    return jsonify(containers)

@app.route('/api/check/<container_id>', methods=['POST'])
def check_update(container_id):
    if not client: return jsonify({'error': 'No docker connection'}), 500
    try:
        result = perform_single_check(container_id)
        return jsonify(result)
    except Exception as e:
        logger.error(f"Check failed: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/update/<container_name>', methods=['POST'])
def run_update(container_name):
    if not client: return jsonify({'error': 'No docker connection'}), 500
    try:
        old_image_id = None
        container_id = None
        containers = client.containers.list(filters={'name': container_name})
        for c in containers:
            if c.name == container_name:
                old_image_id = c.image.id
                container_id = c.id
                break
        dependents = collect_dependents_if_enabled(container_id, container_name) if container_id else []
        trigger_updater_engine(container_name, old_image_id)
        restarted = restart_collected_dependents(dependents, container_name)
        return jsonify({'success': True, 'message': f'Update triggered for {container_name}', 'restarted_dependents': restarted})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)