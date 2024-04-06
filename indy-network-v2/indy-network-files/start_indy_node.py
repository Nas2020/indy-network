import sys
import os

from indy_node.utils.node_runner import run_node
from indy_common.config_util import getConfig

def create_network_dir(config):
    node_info_dir = config.NODE_INFO_DIR  # Assuming this is the path to 'node_info'
    network_name = config.NETWORK_NAME  # Assuming NETWORK_NAME is defined in config
    
    # Construct the full path to the network-specific directory
    network_dir = os.path.join(node_info_dir, network_name)
    
    # Create the directory if it doesn't exist
    if not os.path.exists(network_dir):
        os.makedirs(network_dir)
        print(f"Created directory: {network_dir}")
    else:
        print(f"Directory already exists: {network_dir}")

if __name__ == "__main__":
    if len(sys.argv) < 6:
        raise Exception("Provide name and two pairs of IP/port for running the node "
                        "and client stacks in form 'node_name node_ip node_port client_ip client_port'")

    config = getConfig()
    create_network_dir(config)  # Ensure the network directory exists before proceeding

    self_name = sys.argv[1]
    run_node(config, self_name,
             node_ip=sys.argv[2], node_port=int(sys.argv[3]),
             client_ip=sys.argv[4], client_port=int(sys.argv[5]))
