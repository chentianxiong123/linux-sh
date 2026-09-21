#!/bin/bash

status=$(sudo -n systemctl is-active docker 2>/dev/null)

if [ "$status" = "active" ]; then
    sudo -n systemctl stop docker.socket docker.service
    notify-send "Docker" "已关闭" -i docker
else
    sudo -n systemctl start docker.service
    sleep 1
    notify-send "Docker" "已开启" -i docker
fi
