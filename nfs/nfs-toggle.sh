#!/bin/bash

NFS_SERVER="192.168.31.82"
NFS_PATH="/mnt/shared"
MOUNT_POINT="/mnt/shared"

if mountpoint -q "$MOUNT_POINT" 2>/dev/null; then
    echo '123456' | sudo -S umount "$MOUNT_POINT" 2>/dev/null
    if [ $? -eq 0 ]; then
        notify-send "NFS" "已卸载" -i folder-remote
    else
        notify-send "NFS" "卸载失败" -i dialog-error
    fi
else
    mkdir -p "$MOUNT_POINT"
    echo '123456' | sudo -S mount -t nfs "${NFS_SERVER}:${NFS_PATH}" "$MOUNT_POINT" 2>/dev/null
    if [ $? -eq 0 ]; then
        notify-send "NFS" "已挂载: $MOUNT_POINT" -i folder-remote
    else
        notify-send "NFS" "挂载失败" -i dialog-error
    fi
fi