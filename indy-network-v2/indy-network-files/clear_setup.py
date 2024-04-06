import os
import shutil
import argparse
import errno
from indy_common.config_helper import ConfigHelper
from indy_common.config_util import getConfig

# Function to safely remove directories if they exist
def safe_remove_dir(path):
    try:
        if os.path.exists(path):
            shutil.rmtree(path)
    except OSError as e:
        if e.errno == errno.ENOTEMPTY or e.errno == errno.EBUSY:
            print(f"Warning: Could not remove {path} because it is busy or not empty.")
        else:
            raise

def clean(config, full, network_name):
    if network_name:
        config.NETWORK_NAME = network_name
    config_helper = ConfigHelper(config)

    # Remove directories safely using the global safe_remove_dir function
    safe_remove_dir(config_helper.keys_dir)
    safe_remove_dir(config_helper.genesis_dir)
    safe_remove_dir(config_helper.log_dir)

    if full:
        safe_remove_dir(config_helper.ledger_base_dir)
        safe_remove_dir(config_helper.log_base_dir)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Removes nodes data and configuration')
    parser.add_argument('--full', action='store_true', help="remove configs and logs too")
    parser.add_argument('--network', required=False, type=str, help="Network to clean")
    args = parser.parse_args()
    config = getConfig()
    clean(config, args.full, args.network)
