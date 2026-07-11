#!/bin/bash
# Liberta todas as teclas e botões de rato que possam estar presos via ydotool
echo "A libertar teclas e botões presos..."

# Liberta todas as teclas usadas nos scripts (keyup explícito de cada uma)
for code in 1 2 3 4 5 6 7 8 9 10 11 16 17 18 19 20 21 22 23 24 25 30 31 32 33 34 35 36 37 38 44 45 46 47 48 49 50 57 14 15 28 52 51; do
    ydotool key "${code}:0" 2>/dev/null
done

# Liberta botões do rato (left, right, middle)
ydotool click 0x80 2>/dev/null  # left up
ydotool click 0x81 2>/dev/null  # right up
ydotool click 0x82 2>/dev/null  # middle up

echo "Limpeza concluída."
