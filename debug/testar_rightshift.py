import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from infra_bridge import pydirectinput

print("==================================================")
print(">>> TESTE DA TECLA 'RIGHT SHIFT' (via automacao)")
print("==================================================")
print("Muda já para a janela do Elite Dangerous.")
print("Vou enviar 'right shift' daqui a 3 segundos...\n")

for i in (3, 2, 1):
    print(i)
    time.sleep(1)

pydirectinput.press('rightshift')
print("\n[ENVIADO] right shift premido pela automação.")
print("Confirma no jogo: acelerou para 100%?")
