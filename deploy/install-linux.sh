#!/bin/bash
# Install RemoteLLM services on Linux (systemd)
# Usage: sudo ./install-linux.sh [connector|broker|both]

set -e

INSTALL_DIR="/opt/remotellm"
CONFIG_DIR="/etc/remotellm"
LOG_DIR="/var/log/remotellm"
SERVICE_USER="remotellm"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Check if running as root
if [[ $EUID -ne 0 ]]; then
    log_error "This script must be run as root (use sudo)"
    exit 1
fi

# Parse arguments
COMPONENT="${1:-both}"

if [[ "$COMPONENT" != "connector" && "$COMPONENT" != "broker" && "$COMPONENT" != "both" ]]; then
    log_error "Usage: $0 [connector|broker|both]"
    exit 1
fi

log_info "Installing RemoteLLM ($COMPONENT)"

# Create user if doesn't exist
if ! id "$SERVICE_USER" &>/dev/null; then
    log_info "Creating service user: $SERVICE_USER"
    useradd --system --no-create-home --shell /bin/false "$SERVICE_USER"
fi

# Create directories
log_info "Creating directories"
mkdir -p "$INSTALL_DIR"
mkdir -p "$CONFIG_DIR"
mkdir -p "$LOG_DIR"

# Set permissions
chown "$SERVICE_USER:$SERVICE_USER" "$LOG_DIR"

# Install Python package
log_info "Installing RemoteLLM package"
python3 -m venv "$INSTALL_DIR/venv"
"$INSTALL_DIR/venv/bin/pip" install --upgrade pip
"$INSTALL_DIR/venv/bin/pip" install remotellm

# Install connector service
if [[ "$COMPONENT" == "connector" || "$COMPONENT" == "both" ]]; then
    log_info "Installing connector service"

    # Copy service file
    cp "$(dirname "$0")/systemd/remotellm-connector.service" /etc/systemd/system/

    # Create example config if not exists
    if [[ ! -f "$CONFIG_DIR/connector.env" ]]; then
        cat > "$CONFIG_DIR/connector.env" << 'EOF'
# RemoteLLM Connector Configuration
# Edit these values before starting the service

# URL of the local LLM server (OpenAI-compatible API)
REMOTELLM_LLM_URL=http://localhost:11434

# WebSocket URL of the broker to connect to
REMOTELLM_BROKER_URL=wss://broker.example.com:8444

# Authentication token for this connector (must match broker config)
REMOTELLM_BROKER_TOKEN=your-token-here

# Comma-separated list of model names served by this connector
# These models will be registered with the broker for routing
REMOTELLM_MODELS=gpt-4,gpt-3.5-turbo

# Logging level (DEBUG, INFO, WARNING, ERROR)
REMOTELLM_LOG_LEVEL=INFO

# Port for health check endpoint
REMOTELLM_HEALTH_PORT=8080
EOF
        chmod 600 "$CONFIG_DIR/connector.env"
        log_warn "Edit $CONFIG_DIR/connector.env before starting the service"
    fi

    # Reload systemd
    systemctl daemon-reload

    log_info "Connector service installed"
    log_info "  Start:   sudo systemctl start remotellm-connector"
    log_info "  Enable:  sudo systemctl enable remotellm-connector"
    log_info "  Status:  sudo systemctl status remotellm-connector"
    log_info "  Logs:    sudo journalctl -u remotellm-connector -f"
fi

# Install broker service
if [[ "$COMPONENT" == "broker" || "$COMPONENT" == "both" ]]; then
    log_info "Installing broker service"

    # Copy service file
    cp "$(dirname "$0")/systemd/remotellm-broker.service" /etc/systemd/system/

    # Create example config if not exists
    if [[ ! -f "$CONFIG_DIR/broker.env" ]]; then
        cat > "$CONFIG_DIR/broker.env" << 'EOF'
# RemoteLLM Broker Configuration
# Edit these values before starting the service

# Host and port to bind the HTTP API
REMOTELLM_BROKER_HOST=0.0.0.0
REMOTELLM_BROKER_PORT=8443

# Comma-separated list of valid connector authentication tokens
REMOTELLM_BROKER_CONNECTOR_TOKENS=connector-token-1,connector-token-2

# Comma-separated list of valid user API keys for the OpenAI-compatible API
REMOTELLM_BROKER_USER_API_KEYS=user-api-key-1,user-api-key-2

# Path to YAML file with connector configurations (token -> llm_api_key mapping)
# See /etc/remotellm/connectors.yaml.example
REMOTELLM_BROKER_CONNECTOR_CONFIG=/etc/remotellm/connectors.yaml

# Logging level (DEBUG, INFO, WARNING, ERROR)
REMOTELLM_BROKER_LOG_LEVEL=INFO

# Port for health check endpoint
REMOTELLM_BROKER_HEALTH_PORT=8080
EOF
        chmod 600 "$CONFIG_DIR/broker.env"
        log_warn "Edit $CONFIG_DIR/broker.env before starting the service"
    fi

    # Copy example connectors config
    if [[ ! -f "$CONFIG_DIR/connectors.yaml" ]]; then
        cp "$(dirname "$0")/connectors.yaml.example" "$CONFIG_DIR/connectors.yaml.example"
        log_warn "Copy $CONFIG_DIR/connectors.yaml.example to $CONFIG_DIR/connectors.yaml and edit"
    fi

    # Reload systemd
    systemctl daemon-reload

    log_info "Broker service installed"
    log_info "  Start:   sudo systemctl start remotellm-broker"
    log_info "  Enable:  sudo systemctl enable remotellm-broker"
    log_info "  Status:  sudo systemctl status remotellm-broker"
    log_info "  Logs:    sudo journalctl -u remotellm-broker -f"
fi

log_info "Installation complete!"
