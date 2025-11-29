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
    "auto_update_containers": []  # List of container names
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

def trigger_updater_engine(container_name):
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
        remove=True
    )

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
                        
                        for c in containers:
                            if c.short_id in current_hostname: continue
                            image_name = get_image_name(c)
                            if "watchtower" in image_name: continue
                            
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
                                        trigger_updater_engine(c.name)
                                        with CACHE_LOCK:
                                            if c.id in SERVER_CACHE: del SERVER_CACHE[c.id]
                                            
                            except Exception as inner_e:
                                logger.warning(f"Failed to process {c.name}: {inner_e}")
                        
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
    allowed_keys = ["check_mode", "check_interval", "auto_update_mode", "auto_update_containers"]
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
            if c.short_id in current_hostname: continue
            image_name = get_image_name(c)
            if "watchtower" in image_name: continue
            
            container_data = {
                'id': c.id,
                'name': c.name,
                'image': image_name,
                'status': c.status,
                'short_id': c.short_id,
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
        trigger_updater_engine(container_name)
        return jsonify({'success': True, 'message': f'Update triggered for {container_name}'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)