#!/usr/bin/env bash
# 🐢 Sea Turtle — One-click installation script
# Usage: curl -sSL https://raw.githubusercontent.com/haklhl/turtle/main/setup.sh | bash

set -e

echo "🐢 Sea Turtle Installer"
echo "========================"
echo ""

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

# Check Python 3.11+
check_python() {
    if command -v python3 &>/dev/null; then
        PY=python3
    elif command -v python &>/dev/null; then
        PY=python
    else
        echo -e "${RED}❌ Python not found. Please install Python 3.11+${NC}"
        exit 1
    fi

    PY_VERSION=$($PY -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
    PY_MAJOR=$($PY -c "import sys; print(sys.version_info.major)")
    PY_MINOR=$($PY -c "import sys; print(sys.version_info.minor)")

    if [ "$PY_MAJOR" -lt 3 ] || ([ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 11 ]); then
        echo -e "${RED}❌ Python $PY_VERSION found, but 3.11+ is required.${NC}"
        exit 1
    fi

    echo -e "${GREEN}✅ Python $PY_VERSION${NC}"
}

# Create data directory
setup_dirs() {
    DATA_DIR="$HOME/.sea_turtle"
    mkdir -p "$DATA_DIR"
    mkdir -p "$DATA_DIR/logs"
    mkdir -p "$DATA_DIR/logs/agents"
    echo -e "${GREEN}✅ Data directory: $DATA_DIR${NC}"
}

# Create virtual environment
setup_venv() {
    VENV_DIR="$HOME/.sea_turtle/venv"
    if [ -d "$VENV_DIR" ]; then
        echo -e "${YELLOW}ℹ️  Virtual environment already exists: $VENV_DIR${NC}"
    else
        echo "Creating virtual environment..."
        $PY -m venv "$VENV_DIR"
        echo -e "${GREEN}✅ Virtual environment created: $VENV_DIR${NC}"
    fi

    # Activate
    source "$VENV_DIR/bin/activate"
}

# Install package
install_package() {
    echo "Installing sea-turtle..."
    pip install --upgrade pip -q
    pip install sea-turtle -q 2>/dev/null || {
        # If not on PyPI yet, install from GitHub
        echo "Installing from GitHub..."
        pip install git+https://github.com/haklhl/turtle.git -q
    }
    echo -e "${GREEN}✅ sea-turtle installed${NC}"
}

# Add to PATH
setup_path() {
    VENV_BIN="$HOME/.sea_turtle/venv/bin"
    SHELL_RC=""

    if [ -n "$ZSH_VERSION" ] || [ -f "$HOME/.zshrc" ]; then
        SHELL_RC="$HOME/.zshrc"
    elif [ -f "$HOME/.bashrc" ]; then
        SHELL_RC="$HOME/.bashrc"
    elif [ -f "$HOME/.bash_profile" ]; then
        SHELL_RC="$HOME/.bash_profile"
    fi

    if [ -n "$SHELL_RC" ]; then
        if ! grep -q "sea_turtle/venv/bin" "$SHELL_RC" 2>/dev/null; then
            echo "" >> "$SHELL_RC"
            echo "# Sea Turtle" >> "$SHELL_RC"
            echo "export PATH=\"$VENV_BIN:\$PATH\"" >> "$SHELL_RC"
            echo -e "${GREEN}✅ Added to PATH in $SHELL_RC${NC}"
        fi
    fi

    # Also create alias
    if [ -n "$SHELL_RC" ]; then
        if ! grep -q "alias st=" "$SHELL_RC" 2>/dev/null; then
            echo "alias st='seaturtle'" >> "$SHELL_RC"
            echo -e "${GREEN}✅ Alias 'st' created${NC}"
        fi
    fi
}

# Run onboard
run_onboard() {
    echo ""
    echo "🐢 Running setup wizard..."
    echo ""
    seaturtle onboard
}

# Main
main() {
    check_python
    setup_dirs
    setup_venv
    install_package
    setup_path
    run_onboard

    echo ""
    echo "========================"
    echo -e "${GREEN}🐢 Sea Turtle installation complete!${NC}"
    echo ""
    echo "Quick start:"
    echo "  source ~/.sea_turtle/venv/bin/activate"
    echo "  seaturtle start"
    echo ""
    echo "Or register as a service:"
    echo "  seaturtle install-service"
    echo ""
}

main "$@"
