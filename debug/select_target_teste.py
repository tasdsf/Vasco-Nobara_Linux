import os
import sys
import cv2
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from infra_bridge import mss, keyboard

DIRETORIO_PROJETO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IMAGES_DIR = os.path.join(DIRETORIO_PROJETO, "images")

ZONES = {
    'panel': {"top": 200, "left": 50, "width": 1000, "height": 800},
}

# (chave, ficheiro, threshold, zona) — mesmos valores usados no select_target.py
TEMPLATES_CONFIG = {
    'nav_tab':     {'zone': ZONES['panel'], 'file': 'NAVIGATION_SELECTED.png',  'threshold': 0.61},
    'carrier':     {'zone': ZONES['panel'], 'file': 'FLEET_CARRIER_NAME.png',   'threshold': 0.90},
    'station':     {'zone': ZONES['panel'], 'file': 'STATION.png',             'threshold': 0.75},
    'station_alt': {'zone': ZONES['panel'], 'file': 'STATION1.png',            'threshold': 0.75},
    'locked':      {'zone': ZONES['panel'], 'file': 'LOCKED_DESTINATION.png',  'threshold': 0.80},
    'unlocked':    {'zone': ZONES['panel'], 'file': 'UNLOCKED_DESTINATION.png','threshold': 0.80},
}

NOME_JANELA = "R2D2 - Laboratorio de Select Target"

print("[SISTEMA] A carregar templates...")
for chave, config in TEMPLATES_CONFIG.items():
    caminho = os.path.join(IMAGES_DIR, config['file'])
    img = cv2.imread(caminho, cv2.IMREAD_COLOR)
    if img is None:
        print(f"[ERRO] Não encontrei o ficheiro: {config['file']}")
        sys.exit(1)
    config['template'] = img
    print(f"[OK] {config['file']} carregado.")

# CRÍTICO: a sessão PipeWire (e o eventual popup do KDE) tem de arrancar ANTES
# de qualquer janela cv2 — senão o Qt do cv2 colide com o main loop do GLib do
# dbus-python e o CreateSession nunca responde.
print("[SISTEMA] A iniciar sessão de captura...")
with mss.mss() as sct:
    sct.grab({"top": 0, "left": 0, "width": 50, "height": 50})

cv2.namedWindow(NOME_JANELA, cv2.WINDOW_NORMAL)
cv2.resizeWindow(NOME_JANELA, 1280, 720)
# Monitor ultrawide 3840x1080: o jogo ocupa a metade esquerda (0-1920px),
# por isso a janela de debug vai para a metade direita, fora do jogo.
cv2.moveWindow(NOME_JANELA, 1950, 50)

print("\n==================================================")
print(">>> LAB SELECT TARGET ONLINE")
print("-> CAIXA AZUL   : Limite da Zona de Procura (ROI)")
print("-> CAIXAS VERDES: Template Detetado (Match >= threshold)")
print("-> TECLA 'Q'    : Fechar o laboratório")
print("==================================================\n")

with mss.mss() as sct:
    monitores = sct.monitors
    try: monitor_jogo = monitores[1]
    except IndexError: monitor_jogo = monitores[0]

    while True:
        if keyboard.is_pressed('q'):
            print("[SISTEMA] A encerrar laboratório...")
            break

        area_total = {
            "top": monitor_jogo["top"], "left": monitor_jogo["left"],
            "width": monitor_jogo["width"], "height": monitor_jogo["height"]
        }

        try:
            img_bgra_full = np.array(sct.grab(area_total))
            img_bgr_full = cv2.cvtColor(img_bgra_full, cv2.COLOR_BGRA2BGR)
        except Exception:
            continue

        for nome_zona, dims in ZONES.items():
            pt1 = (dims["left"], dims["top"])
            pt2 = (dims["left"] + dims["width"], dims["top"] + dims["height"])
            cv2.rectangle(img_bgr_full, pt1, pt2, (255, 100, 0), 2)
            cv2.putText(img_bgr_full, f"ZONA: {nome_zona.upper()}", (dims["left"] + 5, dims["top"] + 20),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 100, 0), 2)

        y_texto = 30
        cv2.putText(img_bgr_full, "TELEMETRIA DE CORRESPONDENCIA:", (20, y_texto), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)
        y_texto += 35

        for chave, config in TEMPLATES_CONFIG.items():
            zona = config['zone']
            threshold = config['threshold']
            template_img = config['template']
            th, tw = template_img.shape[:2]

            roi_bgr = img_bgr_full[zona['top']:zona['top']+zona['height'], zona['left']:zona['left']+zona['width']]

            res = cv2.matchTemplate(roi_bgr, template_img, cv2.TM_CCOEFF_NORMED)
            _, max_val, _, max_loc = cv2.minMaxLoc(res)

            cor_texto = (0, 0, 255)
            estado = "AUSENTE"

            if max_val >= threshold * 0.7:
                cor_texto = (0, 165, 255)
                estado = "VISIVEL (FRACO)"

            if max_val >= threshold:
                cor_texto = (0, 255, 0)
                estado = "TRANCADO"
                real_x = zona['left'] + max_loc[0]
                real_y = zona['top'] + max_loc[1]
                cv2.rectangle(img_bgr_full, (real_x, real_y), (real_x + tw, real_y + th), (0, 255, 0), 3)
                cv2.putText(img_bgr_full, f"{chave.upper()}", (real_x, real_y - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)

            linha_log = f"[{chave}] -> Match: {max_val*100:.1f}% / {threshold*100:.0f}% | {estado}"
            cv2.putText(img_bgr_full, linha_log, (20, y_texto), cv2.FONT_HERSHEY_SIMPLEX, 0.6, cor_texto, 2)
            y_texto += 30

        cv2.imshow(NOME_JANELA, img_bgr_full)
        cv2.waitKey(40)

cv2.destroyAllWindows()
