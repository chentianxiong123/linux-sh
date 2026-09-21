#!/usr/bin/env bash
# 提取 Windows exe 的图标到 ~/sh/icons/
# 用法: ./extract-icon.sh <exe路径> <输出png名>
# 例:   ./extract-icon.sh "/path/to/app.exe" myapp.png

set -euo pipefail

if [ $# -lt 2 ]; then
    echo "用法: $0 <exe路径> <输出png名>"
    echo "例:   $0 '/home/a1/.wine/drive_c/Program Files/xxx/app.exe' xxx.png"
    exit 1
fi

EXE="$1"
OUT="$HOME/sh/icons/$2"

if [ ! -f "$EXE" ]; then
    echo "❌ 找不到 exe: $EXE" >&2
    exit 1
fi

# 1) 从 exe 里提取最大的 .ico
TMPDIR=$(mktemp -d)
trap "rm -rf $TMPDIR" EXIT

7z e -y "$EXE" -o"$TMPDIR" -r >/dev/null 2>&1

# 找最大的 ico 文件（一般是主图标）
ICO=$(find "$TMPDIR" -iname "*.ico" -printf "%s %p\n" 2>/dev/null | sort -rn | head -1 | cut -d' ' -f2-)

if [ -z "$ICO" ]; then
    echo "❌ exe 里没有 .ico 资源" >&2
    exit 1
fi

# 2) 用 PIL 转 png
python3 -c "
from PIL import Image
im = Image.open('$ICO')
# 选最大的尺寸
best = im
try:
    for i in range(im.n_frames):
        im.seek(i)
        if im.size[0] >= best.size[0]:
            best = im.copy()
except AttributeError:
    pass
best.save('$OUT')
print(f'图标已保存: $OUT ({best.size[0]}x{best.size[1]})')
"
