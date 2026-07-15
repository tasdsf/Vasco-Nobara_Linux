#!/usr/bin/env python3
"""
debug_template_match.py

Script de diagnóstico para a PR #7 (select-target-name-confirm).

Testa se os templates 'confirma_station' (futen_destination_check.png) e
'confirma_carrier' (carrier_destination_confirm.png) fazem match contra
a região MONITOR_PANEL do ecrã, e mostra:
  - confiança do match (0.0 a 1.0)
  - localização exata onde o match foi encontrado
  - screenshot anotado com o retângulo do match (para inspeção visual)
  - screenshot bruto da região capturada (para confirmar se a região está certa)

USO:
    python3 debug_template_match.py

    Corre com o jogo aberto, DEPOIS do painel de seleção de destino já ter
    sido fechado (menu '1') -- é nesse estado que o texto de confirmação
    ("FUTEN SPACEPORT" / "CARRIER (ZAHIR W6G-26N)") fica visível, não com o
    painel ainda aberto. Isto reflete a ordem actual de select_target.py
    (fechar painel -> só depois verificar por nome).

AJUSTA antes de correr:
    - MONITOR_PANEL: coordenadas da região a capturar (linha ~40)
    - TEMPLATES_DIR: caminho da pasta com os .png (linha ~35)
    - THRESHOLD: confiança mínima considerada "match" (linha ~45)
"""

import time
from pathlib import Path

import cv2
import numpy as np

try:
    from infra_bridge import mss
except ImportError:
    print("[AVISO] Não consegui importar infra_bridge.mss — a usar mss diretamente.")
    import mss

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
    "left": 50,
    "width": 1000,
    "height": 800,
}

THRESHOLD = 0.80  # mesmo valor usado em select_target.py (procurar_template(..., 0.80))

OUTPUT_DIR = Path("debug_output")
OUTPUT_DIR.mkdir(exist_ok=True)


def capturar_regiao(monitor: dict) -> np.ndarray:
    """Captura a região definida e devolve como imagem BGR (cv2)."""
    with mss.mss() as sct:
        shot = sct.grab(monitor)
        img = np.array(shot)  # BGRA
        img_bgr = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
    return img_bgr


def testar_template(nome: str, caminho_template: Path, screenshot: np.ndarray) -> dict:
    """Faz template matching e devolve confiança + localização."""
    if not caminho_template.exists():
        return {"nome": nome, "erro": f"Ficheiro não encontrado: {caminho_template}"}

    template = cv2.imread(str(caminho_template), cv2.IMREAD_COLOR)
    if template is None:
        return {"nome": nome, "erro": f"cv2 não conseguiu ler: {caminho_template}"}

    th, tw = template.shape[:2]
    sh, sw = screenshot.shape[:2]

    if th > sh or tw > sw:
        return {
            "nome": nome,
            "erro": (
                f"Template ({tw}x{th}) é maior que a região capturada ({sw}x{sh}). "
                "A região MONITOR_PANEL provavelmente está errada."
            ),
        }

    resultado = cv2.matchTemplate(screenshot, template, cv2.TM_CCOEFF_NORMED)
    _, max_val, _, max_loc = cv2.minMaxLoc(resultado)

    return {
        "nome": nome,
        "confianca": max_val,
        "localizacao": max_loc,  # canto superior-esquerdo do match, relativo à região capturada
        "tamanho_template": (tw, th),
        "match_ok": max_val >= THRESHOLD,
    }


def anotar_e_gravar(screenshot: np.ndarray, resultados: list, timestamp: str):
    """Desenha retângulos dos matches encontrados e grava a imagem anotada."""
    anotada = screenshot.copy()

    for r in resultados:
        if "erro" in r:
            continue
        x, y = r["localizacao"]
        tw, th = r["tamanho_template"]
        cor = (0, 255, 0) if r["match_ok"] else (0, 0, 255)  # verde=ok, vermelho=falhou threshold
        cv2.rectangle(anotada, (x, y), (x + tw, y + th), cor, 2)
        label = f"{r['nome']}: {r['confianca']:.3f}"
        cv2.putText(anotada, label, (x, max(y - 10, 15)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, cor, 2)

    caminho_bruto = OUTPUT_DIR / f"screenshot_bruto_{timestamp}.png"
    caminho_anotado = OUTPUT_DIR / f"screenshot_anotado_{timestamp}.png"
    cv2.imwrite(str(caminho_bruto), screenshot)
    cv2.imwrite(str(caminho_anotado), anotada)

    return caminho_bruto, caminho_anotado


def main():
    print("=" * 60)
    print("DEBUG: Template Matching — confirmação de destino")
    print("=" * 60)
    print(f"Região capturada (MONITOR_PANEL): {MONITOR_PANEL}")
    print(f"Threshold de confiança: {THRESHOLD}")
    print()
    print("A capturar em 3 segundos — certifica-te que o painel de")
    print("seleção de destino já foi FECHADO (menu '1') e que o alvo")
    print("trancado está visível no jogo AGORA.")
    time.sleep(3)

    screenshot = capturar_regiao(MONITOR_PANEL)
    print(f"Screenshot capturado: {screenshot.shape[1]}x{screenshot.shape[0]} px\n")

    resultados = []
    for nome, caminho in TEMPLATES_TO_TEST.items():
        r = testar_template(nome, caminho, screenshot)
        resultados.append(r)

        print(f"--- {nome} ---")
        if "erro" in r:
            print(f"  ERRO: {r['erro']}")
        else:
            status = "✓ MATCH" if r["match_ok"] else "✗ sem match (abaixo do threshold)"
            print(f"  Confiança: {r['confianca']:.4f}  {status}")
            print(f"  Localização (x,y) na região capturada: {r['localizacao']}")
            print(f"  Tamanho do template: {r['tamanho_template']}")
        print()

    timestamp = time.strftime("%Y%m%d_%H%M%S")
    caminho_bruto, caminho_anotado = anotar_e_gravar(screenshot, resultados, timestamp)

    print("=" * 60)
    print(f"Screenshot bruto gravado em:    {caminho_bruto}")
    print(f"Screenshot anotado gravado em:  {caminho_anotado}")
    print("=" * 60)
    print()
    print("PRÓXIMO PASSO:")
    print("Abre o screenshot anotado. Se os retângulos não aparecem")
    print("onde o texto de confirmação está no ecrã real, o problema")
    print("é a região MONITOR_PANEL (coordenadas erradas) ou o template")
    print("está desatualizado (screenshot antigo, resolução diferente).")
    print()
    print("Se a confiança estiver perto do threshold mas não o atingir")
    print("(ex: 0.68 com threshold 0.80), pode bastar baixar o THRESHOLD")
    print("neste script — mas testa em várias situações antes de mudar")
    print("o valor no select_target.py, para não criares falsos positivos.")


if __name__ == "__main__":
    main()
