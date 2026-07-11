import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import select_target as st
from infra_bridge import pydirectinput, keyboard

MONITOR_PANEL = st.MONITOR_PANEL
templates = st.templates

# ==========================================
# Navegação real (igual ao select_target.py) até ao ponto exato onde
# o script normal faria o 'space' que seleciona o alvo e abre o
# submenu com o estado LOCKED/UNLOCKED.
# ==========================================
st.inicializar_infraestrutura()
time.sleep(1)

tipo_alvo = st.obter_alvo_contextual_log()
label_alvo = "STATION" if tipo_alvo == "station" else "CARRIER"
if tipo_alvo == "station":
    template_alvo = [(templates['station'], 0.75), (templates['station_alt'], 0.75)]
else:
    template_alvo = [(templates['carrier'], 0.85)]

print(f"\n>>> A navegar até {label_alvo}...")
pydirectinput.press('1')
time.sleep(1.2)

nav_found = False
for _ in range(8):
    if st.procurar_template(templates['nav_tab'], "NAV TAB", MONITOR_PANEL, 0.61):
        nav_found = True
        break
    pydirectinput.press('q'); time.sleep(0.5)

if not nav_found:
    print("[ERRO] Não encontrei a aba NAVIGATION.")
    sys.exit(1)

pydirectinput.press('d')
time.sleep(0.5)

achou = False
for _ in range(25):
    if st.procurar_template(template_alvo, label_alvo, MONITOR_PANEL):
        achou = True
        break
    pydirectinput.press('s'); time.sleep(0.4)

if not achou:
    print(f"[ERRO] Não encontrei {label_alvo} na lista.")
    sys.exit(1)

print(f"\n>>> Encontrado {label_alvo}. A dar tempo ao popup para renderizar...")
time.sleep(2.4)

print(">>> A premir 'space' UMA ÚNICA vez (a mesma que o select_target.py faz)...")
pydirectinput.press('space')

print("\n==================================================")
print(">>> MODO OBSERVAÇÃO — não premimos mais nenhuma tecla a partir daqui.")
print(">>> Faz tu o que quiseres no jogo (space, backspace, etc.) e observa")
print(">>> como os valores abaixo reagem. Cada linha tem o tempo decorrido")
print(">>> desde o início desta observação, para correlacionares com as")
print(">>> teclas que premires.")
print(">>> TECLA 'k' (neste terminal, não no jogo): sai do laboratório.")
print("==================================================\n")

t0 = time.time()
try:
    while True:
        if keyboard.is_pressed('k'):
            print("[SISTEMA] A encerrar observação...")
            break
        elapsed = time.time() - t0
        print(f"[{elapsed:6.2f}s] ", end="")
        st.procurar_template(templates['locked'], "LOCKED", MONITOR_PANEL, 0.82)
        print(f"[{elapsed:6.2f}s] ", end="")
        st.procurar_template(templates['unlocked'], "UNLOCKED", MONITOR_PANEL, 0.82)
        time.sleep(0.2)
except KeyboardInterrupt:
    print("\n[SISTEMA] Encerrado (Ctrl+C).")
