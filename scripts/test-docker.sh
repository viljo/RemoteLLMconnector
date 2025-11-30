#!/bin/bash
# Run Docker Compose integration tests
#
# Usage:
#   ./scripts/test-docker.sh         # Run tests
#   ./scripts/test-docker.sh --build # Force rebuild images
#   ./scripts/test-docker.sh --clean # Clean up after tests

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_DIR"

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

cleanup() {
    log_info "Cleaning up Docker Compose resources..."
    docker compose -f docker-compose.test.yml down --volumes --remove-orphans 2>/dev/null || true
}

# Parse arguments
BUILD_FLAG=""
CLEAN_ONLY=false

for arg in "$@"; do
    case $arg in
        --build)
            BUILD_FLAG="--build"
            ;;
        --clean)
            CLEAN_ONLY=true
            ;;
        --help|-h)
            echo "Usage: $0 [--build] [--clean]"
            echo ""
            echo "Options:"
            echo "  --build    Force rebuild of Docker images"
            echo "  --clean    Only clean up resources (don't run tests)"
            echo "  --help     Show this help message"
            exit 0
            ;;
    esac
done

# Clean up only mode
if [ "$CLEAN_ONLY" = true ]; then
    cleanup
    log_info "Cleanup complete"
    exit 0
fi

# Trap to ensure cleanup on exit
trap cleanup EXIT

log_info "Starting Docker Compose integration tests..."

# Check for required files
if [ ! -f "docker-compose.test.yml" ]; then
    log_error "docker-compose.test.yml not found"
    exit 1
fi

if [ ! -f "Dockerfile" ]; then
    log_error "Dockerfile not found"
    exit 1
fi

if [ ! -f "Dockerfile.connector" ]; then
    log_error "Dockerfile.connector not found"
    exit 1
fi

# Clean up any previous runs
log_info "Cleaning up previous test runs..."
docker compose -f docker-compose.test.yml down --volumes --remove-orphans 2>/dev/null || true

# Build and run tests
log_info "Building and starting services..."
docker compose -f docker-compose.test.yml up $BUILD_FLAG --abort-on-container-exit --exit-code-from test-runner

EXIT_CODE=$?

if [ $EXIT_CODE -eq 0 ]; then
    log_info "All tests passed!"
else
    log_error "Tests failed with exit code $EXIT_CODE"
fi

exit $EXIT_CODE
