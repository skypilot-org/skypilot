#!/bin/bash

# Usage: sudo SERVER_PORT=... NGINX_PORT=... USERNAME=... PASSWORD=... \
#          /path/to/start_or_reload_nginx_server.bash

# TODO(tian): This script will be called multiple times on the controller. Make this
# script to add a new nginx proxy everytime, instead of overwriting the original one.

# Variables
# SERVER_PORT, NGINX_PORT, USERNAME, PASSWORD are passed as environment variables.
HTPASSWD_FILE="/etc/nginx/.htpasswd"
NGINX_CONF="/etc/nginx/sites-available/default"

# Ensure apache2-utils is installed for htpasswd command
if ! command -v htpasswd &> /dev/null
then
    echo "apache2-utils not found. Installing apache2-utils..."
    sudo apt update
    sudo apt install apache2-utils -y
else
    echo "apache2-utils is already installed."
fi

# Install Nginx if it's not already installed
if ! command -v nginx &> /dev/null
then
    echo "Nginx not found. Installing Nginx..."
    sudo apt update
    sudo apt install nginx -y
else
    echo "Nginx is already installed."
fi

# Create a password file
echo "Creating password file..."
sudo htpasswd -cb $HTPASSWD_FILE $USERNAME $PASSWORD

# Create Nginx configuration
cat > /tmp/nginx_conf.tmp <<EOF
server {
    listen $NGINX_PORT;
    server_name _;

    location / {
        auth_basic "Restricted Content";
        auth_basic_user_file $HTPASSWD_FILE;
        proxy_pass http://0.0.0.0:$SERVER_PORT;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }
}
EOF

# Move the new Nginx configuration to the sites-available directory
echo "Configuring Nginx..."
sudo mv /tmp/nginx_conf.tmp $NGINX_CONF

# Reload Nginx to apply changes
echo "Reloading Nginx..."
sudo nginx -t && sudo systemctl reload nginx
echo "Nginx has been configured and reloaded."
