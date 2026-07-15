#!/data/data/com.termux/files/usr/bin/bash
# ─────────────────────────────────────────────
# Jarvis Auto-Restart Supervisor
# ─────────────────────────────────────────────
# Kya karta hai: server.py chalata hai, aur agar kabhi bhi woh crash ho jaaye
# (kisi bhi wajah se — chahe koi native library panic ho ya kuch aur), toh
# 2 second ruk ke khud-ba-khud phir se start kar deta hai. Isse Jarvis
# "permanently frozen/dead" kabhi nahi rahega — worst case 2-3 second mein
# wapas zinda ho jayega.
#
# Use karne ka tarika:
#   chmod +x run.sh
#   ./run.sh
#
# Rokne ke liye: Ctrl+C (do baar agar zaroorat pade)
# ─────────────────────────────────────────────

cd "$(dirname "$0")"

echo "🤖 Jarvis Supervisor shuru ho raha hai..."
echo "   (Ctrl+C do baar dabao poori tarah band karne ke liye)"
echo ""

trap 'echo ""; echo "🛑 Jarvis band ho raha hai..."; exit 0' INT TERM

RESTART_COUNT=0

while true; do
    echo "▶️  Jarvis server start ho raha hai... (restart #$RESTART_COUNT)"
    python server.py
    EXIT_CODE=$?

    if [ "$EXIT_CODE" -eq 0 ]; then
        echo "✅ Jarvis normally band hua. Bye!"
        break
    fi

    RESTART_COUNT=$((RESTART_COUNT + 1))
    echo "⚠️  Jarvis crash ho gaya (exit code: $EXIT_CODE). 2 second mein restart..."
    sleep 2
done
