"""Vsphere custom script."""
CUSTOMIZED_SCRIPT = """#!/bin/bash
if [ x$1 = x"precustomization" ]; then
    sudo mkdir -p /home/user_placeholder/.ssh
    sudo echo "ssh_public_key" | sudo tee /home/user_placeholder/.ssh/authorized_keys
    sudo mkdir -p /home/user_placeholder/.ssh/pre
    sudo chown -R user_placeholder:user_placeholder /home/user_placeholder/.ssh
    sudo chmod 700 /home/user_placeholder/.ssh
    sudo chmod 644 /home/user_placeholder/.ssh/authorized_keys
fi"""
