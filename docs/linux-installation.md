If you're using Ubuntu Linux, run the project directly from a local source checkout.

## Prerequisites

Install system prerequisites and `uv` if needed:

```bash
sudo apt update
sudo apt install curl
curl -LsSf https://astral.sh/uv/install.sh | sh
source ~/.bashrc
```

**Note:** You may need to restart your terminal or run `exec bash` instead of `source ~/.bashrc` for the PATH changes to take effect.

## Installation

```bash
git clone https://github.com/BoPeng/ai-marketplace-monitor.git
cd ai-marketplace-monitor
uv sync
playwright install
```

If prompted to install Playwright system dependencies, run:

```bash
playwright install-deps
```

## Configuration

Edit the repository-local configuration file using your preferred text editor:

```bash
# Using nano
nano .ai-marketplace-monitor/config.toml

# Using vim
vim .ai-marketplace-monitor/config.toml

# Or install a code editor via snap (recommended method for VS Code)
sudo snap install code --classic
```

## Verification

To verify the setup was successful:

```bash
python monitor.py --version
```

## Troubleshooting

- If playwright browsers fail to install, you may need to install additional system dependencies with `sudo apt install libnss3-dev libatk-bridge2.0-dev libdrm2-dev`
