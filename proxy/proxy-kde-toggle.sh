#!/bin/bash

PROXY_HOST="192.168.31.82"
PROXY_PORT="7890"
PROXY_ADDR="http://${PROXY_HOST}:${PROXY_PORT}"

kde_type=$(kreadconfig5 --file kioslaverc --group "Proxy Settings" --key "ProxyType" 2>/dev/null)

if [ "$kde_type" = "1" ]; then
    kwriteconfig5 --file kioslaverc --group "Proxy Settings" --key "ProxyType" 0
    dbus-send --type=signal --dest=org.kde.kded5 /kded org.kde.kded5.reloadConfiguration
    notify-send "KDE代理" "已关闭" -i network-proxy
else
    kwriteconfig5 --file kioslaverc --group "Proxy Settings" --key "ProxyType" 1
    kwriteconfig5 --file kioslaverc --group "Proxy Settings" --key "httpProxy" " ${PROXY_ADDR} "
    kwriteconfig5 --file kioslaverc --group "Proxy Settings" --key "httpsProxy" " ${PROXY_ADDR} "
    kwriteconfig5 --file kioslaverc --group "Proxy Settings" --key "NoProxyFor" "localhost,127.0.0.1,192.168.0.0/16"
    dbus-send --type=signal --dest=org.kde.kded5 /kded org.kde.kded5.reloadConfiguration
    notify-send "KDE代理" "已开启 -> $PROXY_ADDR" -i network-proxy
fi