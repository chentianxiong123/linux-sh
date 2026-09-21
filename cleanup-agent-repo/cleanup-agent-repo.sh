#!/usr/bin/env bash
set -euo pipefail

# ============================================================
#  cleanup-agent-repo.sh
#  裁剪项目目录，使其成为干净的 Agent 参考知识库
#  保留：源码、文档、配置、lock 文件、README、CHANGELOG
#  删除：测试、二进制、图片资源、构建产物、打包 app
# ============================================================

if [[ $# -lt 1 ]]; then
  echo "用法: $0 <项目目录> [--dry-run]"
  echo "  --dry-run  只打印预览，不实际删除"
  exit 1
fi

TARGET="$1"
DRY_RUN=false
[[ "${2:-}" == "--dry-run" ]] && DRY_RUN=true

if [[ ! -d "$TARGET" ]]; then
  echo "错误: 目录不存在: $TARGET"
  exit 1
fi

BEFORE=$(du -sh "$TARGET" | cut -f1)
echo "目标: $TARGET"
echo "清理前: $BEFORE"
echo "---"

# ---- 0. 删除 .git 目录（归档用途）----
echo "[0/5] 删除 .git 目录..."
if [[ -d "$TARGET/.git" ]]; then
  git_size=$(du -sh "$TARGET/.git" | cut -f1)
  if $DRY_RUN; then
    echo "  [dry-run] 将删除: $TARGET/.git ($git_size)"
  else
    rm -rf "$TARGET/.git"
    echo "  已删除: $TARGET/.git ($git_size)"
  fi
fi

# ---- 1. 删除测试目录 ----
echo "[1/4] 删除测试目录..."
for d in tests tests-js test e2e __tests__; do
  if [[ -d "$TARGET/$d" ]]; then
    if $DRY_RUN; then
      echo "  [dry-run] 将删除目录: $TARGET/$d ($(du -sh "$TARGET/$d" | cut -f1))"
    else
      rm -rf "$TARGET/$d"
      echo "  已删除: $TARGET/$d"
    fi
  fi
done

# ---- 2. 删除测试文件（*.test.*, *.spec.*）----
echo "[2/4] 删除测试文件..."
TEST_COUNT=0
while IFS= read -r -d '' f; do
  TEST_COUNT=$((TEST_COUNT + 1))
  if $DRY_RUN; then
    echo "  [dry-run] 将删除: $f"
  else
    rm -f "$f"
  fi
done < <(find "$TARGET" -type f \( -name "*.test.*" -o -name "*.spec.*" \) -print0)
if $DRY_RUN; then
  echo "  共发现 $TEST_COUNT 个测试文件"
else
  echo "  已删除 $TEST_COUNT 个测试文件"
fi

# ---- 3. 删除图片和二进制资源 ----
echo "[3/4] 删除图片资源和二进制..."
IMG_COUNT=0
BIN_COUNT=0
while IFS= read -r -d '' f; do
  IMG_COUNT=$((IMG_COUNT + 1))
  if $DRY_RUN; then
    echo "  [dry-run] 将删除: $f"
  else
    rm -f "$f"
  fi
done < <(find "$TARGET" -type f \( \
  -iname "*.png" -o -iname "*.jpg" -o -iname "*.jpeg" \
  -o -iname "*.gif" -o -iname "*.webp" -o -iname "*.bmp" \
  -o -iname "*.ico" -o -iname "*.icns" -o -iname "*.ttf" \
  -o -iname "*.otf" -o -iname "*.woff" -o -iname "*.woff2" \
  \) -print0)

while IFS= read -r -d '' f; do
  BIN_COUNT=$((BIN_COUNT + 1))
  if $DRY_RUN; then
    echo "  [dry-run] 将删除: $f"
  else
    rm -f "$f"
  fi
done < <(find "$TARGET" -type f \( \
  -iname "*.exe" -o -iname "*.dll" -o -iname "*.so" \
  -o -iname "*.dylib" -o -iname "*.bin" -o -iname "*.o" \
  -o -iname "*.a" -o -iname "*.pyc" -o -iname "*.pyo" \
  -o -iname "*.class" -o -iname "*.jar" -o -iname "*.whl" \
  -o -iname "*.egg" \
  \) -print0)

if $DRY_RUN; then
  echo "  发现 $IMG_COUNT 个图片文件, $BIN_COUNT 个二进制文件"
else
  echo "  已删除 $IMG_COUNT 个图片文件, $BIN_COUNT 个二进制文件"
fi

# ---- 4. 删除构建/打包产物目录 ----
echo "[4/4] 删除构建产物和打包目录..."
BUILD_DIRS=()
for d in dist build .next out node_modules; do
  find "$TARGET" -maxdepth 3 -type d -name "$d" -print0 | while IFS= read -r -d '' bd; do
    BUILD_DIRS+=("$bd")
    if $DRY_RUN; then
      echo "  [dry-run] 将删除目录: $bd"
    else
      rm -rf "$bd"
      echo "  已删除: $bd"
    fi
  done
done

# lock 文件单独处理（是文件不是目录）
for lf in pnpm-lock.yaml package-lock.json yarn.lock npm-shrinkwrap.json uv.lock poetry.lock Cargo.lock go.sum Gemfile.lock; do
  find "$TARGET" -maxdepth 2 -type f -name "$lf" -print0 | while IFS= read -r -d '' f; do
    if $DRY_RUN; then
      echo "  [dry-run] 将删除 lock 文件: $f"
    else
      rm -f "$f"
      echo "  已删除 lock 文件: $f"
    fi
  done
done

# apps/ 下只保留 src，删掉打包产物子目录
for app_dir in macos android ios linux windows; do
  if [[ -d "$TARGET/apps/$app_dir" ]]; then
    for sub in dist build out node_modules; do
      if [[ -d "$TARGET/apps/$app_dir/$sub" ]]; then
        if $DRY_RUN; then
          echo "  [dry-run] 将删除: $TARGET/apps/$app_dir/$sub"
        else
          rm -rf "$TARGET/apps/$app_dir/$sub"
          echo "  已删除: $TARGET/apps/$app_dir/$sub"
        fi
      fi
    done
  fi
done

# ---- 最终统计 ----
AFTER=$(du -sk "$TARGET" | cut -f1)
echo "---"
echo "清理前: $BEFORE"
echo "清理后: $(du -sh "$TARGET" | cut -f1)"
if $DRY_RUN; then
  echo "(dry-run 模式，未实际删除)"
else
  SAVED=$(( $(du -sk "$TARGET" | cut -f1) > $AFTER ? $(du -sk "$TARGET" | cut -f1) - $AFTER : 0 ))
  echo "节省:   $((SAVED / 1024))M"
fi
echo "完成！"
