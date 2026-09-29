#!/bin/bash
# tmp-clean — 一键清空 /tmp (非 sudo)
#
# 规则:
#   - 尝试删除 /tmp 下的每一项
#   - 能删就删, 不能删就跳过
#   - 遇到 root 拥有的东西静默跳过
#   - 不需要 sudo

cd /tmp || exit 1

total_freed=0
total_files=0
total_dirs=0

for item in * .[!.]* ..?*; do
    [ -e "$item" ] || continue

    # 先记录是文件还是目录, 删除后才能判断
    if [ -d "$item" ]; then
        is_dir=1
    else
        is_dir=0
    fi

    size_k=$(du -sk -- "$item" 2>/dev/null | awk '{print $1}')
    [ -z "$size_k" ] && size_k=0

    if rm -rf -- "$item" 2>/dev/null; then
        if [ $is_dir -eq 1 ]; then
            ((total_dirs++))
        else
            ((total_files++))
        fi
        ((total_freed += size_k))
    fi
done

echo ""
echo "🗑️  /tmp 清理完成"
echo ""
echo "  删除文件: $total_files 个"
echo "  删除目录: $total_dirs 个"
echo "  释放空间: $((total_freed)) KB ($((total_freed/1024)) MB)"
echo ""

# 当前 /tmp 剩余
remain_k=$(du -sk /tmp 2>/dev/null | awk '{print $1}')
echo "  剩余:     ${remain_k} KB (root 所有的系统文件,无法清)"
echo ""
