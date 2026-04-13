#!/bin/bash
# Hook: Auto-resize images before they enter Claude's context window.
# Tracks per-session count. Target shrinks on smooth curve: 1800/(1 + N*0.15), floor 300.
# Outputs scale factor for coordinate translation: click = (img_coord * scale) / DPR
#
# Coordinate algorithm (for clicking on UI from resized screenshots):
#   scale = original_width / resized_width  (uniform — same for both axes)
#   DPR = 2 (Retina)
#   click_x = (img_x * scale) / DPR
#   click_y = (img_y * scale) / DPR
#
# Example: 3024x1618 screenshot resized to 1500x804
#   scale = 3024/1500 = 2.016
#   Button at (750, 400) in resized image → click at (750*2.016/2, 400*2.016/2) = (756, 403)

INPUT=$(cat)
FILE_PATH=$(echo "$INPUT" | jq -r '.tool_input.file_path')
SESSION_ID=$(echo "$INPUT" | jq -r '.session_id')

[[ "$FILE_PATH" =~ \.(png|jpg|jpeg|gif|webp|bmp|PNG|JPG|JPEG|GIF|WEBP|BMP)$ ]] || exit 0
[[ -f "$FILE_PATH" ]] || exit 0

# Count images this session
CF="/tmp/claude-img-count-${SESSION_ID}"
COUNT=$(( $(cat "$CF" 2>/dev/null || echo 0) + 1 ))
echo "$COUNT" > "$CF"

# Smooth decay: max_dim = 1800 / (1 + count * 0.15), floor 300
MAX_DIM=$(( 180000 / (100 + COUNT * 15) ))
[[ "$MAX_DIM" -lt 300 ]] && MAX_DIM=300

# Get original dimensions
W=$(sips -g pixelWidth "$FILE_PATH" 2>/dev/null | tail -1 | awk '{print $2}')
H=$(sips -g pixelHeight "$FILE_PATH" 2>/dev/null | tail -1 | awk '{print $2}')

# Already under target — pass through, no scale needed
if [[ "$W" -le "$MAX_DIM" ]] && [[ "$H" -le "$MAX_DIM" ]]; then
  exit 0
fi

# Resize copy
R="/tmp/claude-resized-$(basename "$FILE_PATH")"
cp "$FILE_PATH" "$R"
sips --resampleHeightWidthMax "$MAX_DIM" "$R" >/dev/null 2>&1

# Get resized dimensions for scale calculation
RW=$(sips -g pixelWidth "$R" 2>/dev/null | tail -1 | awk '{print $2}')
RH=$(sips -g pixelHeight "$R" 2>/dev/null | tail -1 | awk '{print $2}')

# Scale factor (integer math: multiply by 1000 for 3 decimal places)
SCALE_X1000=$(( W * 1000 / RW ))

jq -n --arg path "$R" --argjson n "$COUNT" --argjson m "$MAX_DIM" \
      --arg ow "$W" --arg oh "$H" --arg rw "$RW" --arg rh "$RH" \
      --argjson s "$SCALE_X1000" '{
  hookSpecificOutput: {
    hookEventName: "PreToolUse",
    permissionDecision: "allow",
    updatedInput: { file_path: $path },
    additionalContext: ("#" + ($n|tostring) + " " + $ow + "x" + $oh + "→" + $rw + "x" + $rh + " scale:" + (($s / 1000 * 100 | floor) / 100 | tostring) + " click=(img×" + (($s / 1000 * 100 | floor) / 100 | tostring) + "/2)")
  }
}'
