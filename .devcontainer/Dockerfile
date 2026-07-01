FROM mcr.microsoft.com/devcontainers/python:3.13

# Install system-level dependencies
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        curl \
        git \
        docker-cli \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Switch to non-root user vscode (default user in Microsoft's Python devcontainer image)
USER vscode

# Pre-create bind mount target directories
RUN mkdir -p /home/vscode/.roo/skills \
    && mkdir -p /home/vscode/.vscode-server/data/User/globalStorage/zoocodeorganization.zoo-code/settings


