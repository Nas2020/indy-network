#!/bin/bash

# Create /etc/indy directory if it doesn't exist
mkdir -p /etc/indy

cat <<EOF > /etc/indy/indy_config.py
NETWORK_NAME = '${NETWORK_NAME}'
KEYS_DIR = '/var/lib/indy/keys'
LEDGER_DIR = '/var/lib/indy/ledger'
GENESIS_DIR = '/var/lib/indy/genesis'
LOG_DIR = '/var/lib/indy/log'
PLUGINS_DIR = '/var/lib/indy/plugins'
NODE_INFO_DIR = '/var/lib/indy/node_info'
EOF

mkdir -p /var/lib/indy/keys
mkdir -p /var/lib/indy/ledger
mkdir -p /var/lib/indy/genesis
mkdir -p /var/lib/indy/log
mkdir -p /var/lib/indy/plugins
mkdir -p /var/lib/indy/node_info

if [ ! -f "/var/lib/indy/initialized" ]; then
  echo "Running setup scripts..."
  sudo python3 /opt/indy-network/clear_setup.py --full --network ${NETWORK_NAME} || { echo "Failed to clear setup"; exit 1; }
  
  sudo python3 /opt/indy-network/init_indy_node.py \
  --name ${NODE_NAME} \
  --seed ${NODE_SEED} || { echo "Failed at init_indy_node"; exit 1; }

  sudo python3 /opt/indy-network/create_domain_ledger_genesis_file.py \
  --stewardDids "${STEWARD_DIDS}" \
  --stewardVerkeys "${STEWARD_VERKEYS}" \
  --trusteeDids "${TRUSTEE_DIDS}" \
  --trusteeVerkeys "${TRUSTEE_VERKEYS}" \
  --network ${NETWORK_NAME} || { echo "Failed at create domain ledger genesis file"; exit 1; }

  sudo python3 /opt/indy-network/create_pool_ledger_genesis_file.py \
  --nodeVerkeys "${NODE_VERKEYS}" \
  --nodeBlskeys "${NODE_BLSKEYS}" \
  --nodeBlsProofs "${NODE_BLSPROOFS}" \
  --nodeName "${NODE_NAMES}" \
  --nodePort "${NODE_PORTS}" \
  --clientPort "${CLIENT_PORTS}" \
  --stewardDids "${STEWARD_DIDS}" \
  --nodeNum ${NODE_NUM} \
  --network ${NETWORK_NAME} \
  --ips "${NODE_IPS}" || { echo "Failed at pool ledger genesis file"; exit 1; }

 sudo python3 /opt/indy-network/start_indy_node.py ${NODE_NAME} 0.0.0.0 ${NODE_PORT} 0.0.0.0 ${CLIENT_PORT}

  # Mark initialization as done to prevent rerunning these on restart
  touch /var/lib/indy/initialized
else
  echo "Initialization already done."
fi

# # Change ownership of /var/lib/indy to indyuser:indyuser
# chown -R indyuser:indyuser /var/lib/indy

# Execute the main container command
exec "$@"
