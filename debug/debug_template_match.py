#!/usr/bin/env python3
"""
debug_template_match.py

Script de diagnóstico para a PR #7 (select-target-name-confirm).

Mostra uma janela AO VIVO com a região MONITOR_PANEL capturada em contínuo,
sobrepondo a confiança de match (0.0 a 1.0) de 'confirma_station'
(futen_destination_check.png) e 'confirma_carrier'
(carrier_destination_confirm.png) em tempo real, e desenha o retângulo do
melhor match encontrado.

USO:
    python3 debug_template_match.py

    Corre com o jogo aberto, DEPOIS do painel de seleção de destino já ter
    sido fechado (menu '1') -- é nesse estado que o texto de confirmação
    ("FUTEN SPACEPORT" / "CARRIER (ZAHIR W6G-26N)") fica visível.

Teclas (com a janela em foco):
    's' -- grava screenshot bruto + anotado do frame atual em debug_output/
    'q' ou ESC -- sai

AJUSTA antes de correr:
    - MONITOR_PANEL: coordenadas da região a capturar (linha ~40)
    - TEMPLATES_DIR: caminho da pasta com os .png (linha ~35)
    - THRESHOLD: confiança mínima considerada "match" (linha ~45)
"""

import sys
import time
from pathlib import Path

import cv2
import numpy as np

# infra_bridge.py está na raiz do repo, não em debug/ -- garante que é
# encontrado independentemente da pasta a partir de onde o script é chamado.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from infra_bridge import mss, SCREEN_WIDTH

# ==========================================
# CONFIGURAÇÃO — AJUSTA ISTO
# ==========================================

TEMPLATES_DIR = Path("../images/")  # pasta onde estão os .png

TEMPLATES_TO_TEST = {
    "confirma_station": TEMPLATES_DIR / "futen_destination_check.png",
    "confirma_carrier": TEMPLATES_DIR / "carrier_destination_confirm.png",
}

# Região do ecrã a capturar (MONITOR_PANEL).
# Valores copiados diretamente de select_target.py (linha ~85) -- é a mesma
# região que o bot usa de facto, por isso este teste é representativo.
# Formato mss: {"left": x, "top": y, "width": w, "height": h}
MONITOR_PANEL = {
    "top": 200,
    "left": 30,  # ajustado -20px (era 50) -- teste para separar melhor confirma_station vs confirma_carrier
    "width": 1000,
    "height": 800,
}

THRESHOLD = 0.80  # mesmo valor usado em select_target.py (procurar_template(..., 0.80))

REFRESCO_MS = 150  # intervalo entre capturas (ms) -- ~6-7 fps, suficiente para inspeção visual

OUTPUT_DIR = Path("debug_output")
OUTPUT_DIR.mkdir(exist_ok=True)


def carregar_templates() -> dict:
    templates = {}
    for nome, caminho in TEMPLATES_TO_TEST.items():
        if not caminho.exists():
            print(f"[AVISO] Template não encontrado: {caminho}")
            continue
        t = cv2.imread(str(caminho), cv2.IMREAD_COLOR)
        if t is None:
            print(f"[AVISO] cv2 não conseguiu ler: {caminho}")
            continue
        templates[nome] = t
    return templates


def avaliar_frame(frame: np.ndarray, templates: dict) -> list:
    sh, sw = frame.shape[:2]
    resultados = []
    for nome, template in templates.items():
        th, tw = template.shape[:2]
        if th > sh or tw > sw:
            resultados.append({"nome": nome, "erro": f"template ({tw}x{th}) maior que a região ({sw}x{sh})"})
            continue
        resultado = cv2.matchTemplate(frame, template, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, max_loc = cv2.minMaxLoc(resultado)
        resultados.append({
            "nome": nome,
            "confianca": max_val,
            "localizacao": max_loc,
            "tamanho_template": (tw, th),
            "match_ok": max_val >= THRESHOLD,
        })
    return resultados


def desenhar_overlay(frame: np.ndarray, resultados: list) -> np.ndarray:
    anotado = frame.copy()
    y_txt = 25
    for r in resultados:
        if "erro" in r:
            cv2.putText(anotado, f"{r['nome']}: {r['erro']}", (10, y_txt),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 255), 2)
            y_txt += 25
            continue

        cor = (0, 255, 0) if r["match_ok"] else (0, 0, 255)
        x, y = r["localizacao"]
        tw, th = r["tamanho_template"]
        cv2.rectangle(anotado, (x, y), (x + tw, y + th), cor, 2)

        status = "MATCH" if r["match_ok"] else "sem match"
        linha = f"{r['nome']}: {r['confianca']:.4f} ({status}, threshold {THRESHOLD})"
        cv2.putText(anotado, linha, (10, y_txt), cv2.FONT_HERSHEY_SIMPLEX, 0.55, cor, 2)
        y_txt += 25
    return anotado


def gravar(frame: np.ndarray, anotado: np.ndarray) -> None:
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    caminho_bruto = OUTPUT_DIR / f"screenshot_bruto_{timestamp}.png"
    caminho_anotado = OUTPUT_DIR / f"screenshot_anotado_{timestamp}.png"
    cv2.imwrite(str(caminho_bruto), frame)
    cv2.imwrite(str(caminho_anotado), anotado)
    print(f"[GRAVADO] {caminho_bruto.name} / {caminho_anotado.name}")


def main():
    print("=" * 60)
    print("DEBUG LIVE: Template Matching — confirmação de destino")
    print("=" * 60)
    print(f"Região capturada (MONITOR_PANEL): {MONITOR_PANEL}")
    print(f"Threshold de confiança: {THRESHOLD}")
    print()
    print("Janela ao vivo -- valores atualizam em contínuo.")
    print("Teclas (com a janela em foco): 's' grava | 'q' ou ESC sai.")
    print()

    templates = carregar_templates()
    if not templates:
        print("[FATAL] Nenhum template válido carregado -- a sair.")
        return

    janela = "Debug Match - confirmacao de destino (live)"

    # Força a sessão PipeWire (e o eventual popup do KDE) a resolver-se ANTES
    # de criar a janela -- cv2.namedWindow() antes da sessão ativa colide com
    # o GLib main loop da bridge (ver nota em infra_bridge.py, mesma
    # mitigação usada em olho.py).
    with mss.mss() as sct:
        sct.grab({"top": 0, "left": 0, "width": 50, "height": 50})

    cv2.namedWindow(janela, cv2.WINDOW_NORMAL)

    # Posiciona a janela FORA da área do jogo. O infra_bridge.mss (PipeWire/
    # portal KDE) não expõe geometria real de vários monitores -- por isso,
    # ao contrário do vender.py, não dá para usar monitores[2] (nunca existe
    # aqui). Assume-se um 2º monitor à direita do ecrã do jogo (SCREEN_WIDTH,
    # 0). Se o teu monitor secundário estiver noutra posição, ajusta aqui.
    cv2.moveWindow(janela, SCREEN_WIDTH, 0)
    cv2.setWindowProperty(janela, cv2.WND_PROP_TOPMOST, 1)

    with mss.mss() as sct:
        while True:
            img_bgra = np.array(sct.grab(MONITOR_PANEL))
            frame = cv2.cvtColor(img_bgra, cv2.COLOR_BGRA2BGR)

            resultados = avaliar_frame(frame, templates)
            anotado = desenhar_overlay(frame, resultados)

            cv2.imshow(janela, anotado)
            tecla = cv2.waitKey(REFRESCO_MS) & 0xFF
            if tecla in (ord('q'), 27):
                break
            if tecla == ord('s'):
                gravar(frame, anotado)

    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
