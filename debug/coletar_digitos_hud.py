#!/usr/bin/env python3
"""
coletar_digitos_hud.py

Tester/coletor para o pipeline de leitura da distância no HUD
(supercruise_assist.ler_distancia_hud).

IMPORTANTE (2026-09-17): a etiqueta "distância/nome" está presa ao
alvo no espaço 3D, não a uma posição fixa do ecrã -- a posição muda
consoante a orientação da nave (confirmado ao vivo pelo utilizador:
"o ponto de captura é dinâmico, está preso à estação"). Por isso este
tester NÃO usa MONITOR_DISTANCIA (região fixa, só válida em produção
para o caso estreito já testado) -- procura o texto dentro de uma
"janela" central do ecrã (exclui painéis fixos do HUD), a cada frame,
filtrando por cor em vez de assumir a posição exata.

IMPORTANTE 2 (2026-09-17): há DOIS regimes de cor confirmados --
VERDE na aproximação final ("km") e LARANJA/ÂMBAR em supercruise
("Mm"/"Ls"). No regime laranja a cor sozinha NÃO chega para distinguir
a etiqueta do resto do HUD -- medido: FUEL, o painel INFO e a própria
lista lateral de alvos são exatamente da mesma cor. Por isso a busca
também exclui, por posição, as zonas fixas conhecidas (barra de
mensagens, INFO, lista lateral, dashboard) -- ver JANELA_*_FRAC.

Mostra numa janela ao vivo:
  - a área de busca inteira, com uma caixa à volta de cada caractere
    candidato encontrado (verde = reconhecido com confiança, vermelho
    = não reconhecido) agrupado por linha de texto (mesma "base")
  - por cima de cada linha que chegou a formar uma leitura válida
    (ex: "13.2km"), o texto e o valor interpretado

Ao gravar ('s'), propõe um nome de ficheiro com base na leitura válida
mais alta no ecrã (mais provável ser a etiqueta do alvo, não uma
entrada de lista lateral) -- mas quem decide é sempre a pessoa:
confirma com ENTER ou escreve o valor real. O recorte bruto (só a
zona da linha escolhida, não o ecrã inteiro) fica gravado com esse
nome em debug/hud_distancia/capturas/, para servir de dataset futuro
(o nome do ficheiro É o valor certo, não o que o algoritmo leu).

USO:
    python3 coletar_digitos_hud.py

Teclas (com a janela em foco):
    's' -- propõe nome, pede confirmação/correção no terminal, grava
    'q' ou ESC -- sai
"""

import re
import sys
import time
from pathlib import Path

import cv2
import numpy as np

# infra_bridge.py está na raiz do repo, não em debug/ -- garante que é
# encontrado independentemente da pasta a partir de onde o script é
# chamado. NÃO depende de supercruise_assist.py de propósito: este
# tester tem de continuar a correr mesmo que o pipeline de produção
# esteja a meio de alterações não commitadas (ou revertido, como agora)
# -- por isso traz a sua própria cópia mínima do reconhecedor de
# caracteres, carregada diretamente de images/digitos_hud/.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from infra_bridge import mss, SCREEN_WIDTH

RAIZ_REPO = Path(__file__).resolve().parent.parent
PASTA_DIGITOS_HUD = RAIZ_REPO / "images" / "digitos_hud"
FATOR_UPSCALE_DIGITOS = 8
MAPA_UNIDADE_KM = {"km": 1.0, "Mm": 1000.0, "Ls": 299792.458}


def carregar_digitos_hud():
    mapa_nomes = {"dois_pontos.png": ":"}
    digitos = {}
    if not PASTA_DIGITOS_HUD.is_dir():
        return digitos
    for caminho in PASTA_DIGITOS_HUD.glob("*.png"):
        char = mapa_nomes.get(caminho.name, caminho.stem)
        img = cv2.imread(str(caminho), cv2.IMREAD_COLOR)
        if img is not None:
            digitos[char] = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    return digitos


DIGITOS_HUD = carregar_digitos_hud()


def reconhecer_caractere(blob_bin, threshold=0.60):
    """ Cópia de supercruise_assist._reconhecer_caractere() -- mesma
    lógica (incluindo o limite de escala 0.4-2.5x, ver comentário
    original), duplicada aqui só para este tester não depender do
    módulo de produção. Se a lógica de produção mudar, replicar aqui. """
    melhor_char, melhor_val = None, 0.0
    bh, bw = blob_bin.shape[:2]
    if bh == 0 or bw == 0:
        return None, 0.0
    for char, template in DIGITOS_HUD.items():
        th, tw = template.shape[:2]
        escala = th / bh
        if not (0.4 <= escala <= 2.5):
            continue
        largura_alvo = max(1, int(bw * escala))
        if largura_alvo > tw + 2:
            continue
        blob_redim = cv2.resize(blob_bin, (largura_alvo, th), interpolation=cv2.INTER_NEAREST)
        if largura_alvo > tw:
            largura_alvo = tw
            blob_redim = blob_redim[:, :tw]
        resultado = cv2.matchTemplate(template, blob_redim, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, _ = cv2.minMaxLoc(resultado)
        if max_val > melhor_val:
            melhor_val, melhor_char = max_val, char
    if melhor_val >= threshold:
        return melhor_char, melhor_val
    return None, melhor_val


PASTA_DEBUG = Path(__file__).resolve().parent / "hud_distancia"
PASTA_CAPTURAS = PASTA_DEBUG / "capturas"
PASTA_VISUALIZACOES = PASTA_DEBUG / "visualizacoes"
PASTA_CAPTURAS.mkdir(parents=True, exist_ok=True)
PASTA_VISUALIZACOES.mkdir(parents=True, exist_ok=True)

REFRESCO_MS = 150

# Filtro de cor: a etiqueta de distância/nome do alvo aparece em dois
# regimes de cor confirmados em teste real (2026-09-17):
#   - VERDE (aproximação final, unidade "km"): H~58, S~240
#   - LARANJA/ÂMBAR (supercruise, unidades "Mm"/"Ls"): H~15, S~250
# Os dois intervalos ficam OR'd (cobrem os dois regimes ao mesmo tempo,
# já que não se sabe de antemão qual está ativo). CUIDADO: ao contrário
# do caso verde (onde a cor sozinha já separava a etiqueta do resto),
# no regime laranja TODO o resto do cockpit é da MESMA cor (FUEL, INFO,
# a própria lista lateral de alvos com "NOME ... X.XXMm") -- medido e
# confirmado, não é suposição. Por isso a cor sozinha já não chega:
# ver AREA_BUSCA_* abaixo, que exclui as zonas fixas do HUD (barra de
# mensagens, painel INFO, lista lateral, dashboard) e restringe a
# busca à "janela" central onde só a etiqueta presa ao retículo deve
# aparecer.
HUE_VERDE_MIN, HUE_VERDE_MAX = 30, 90
HUE_LARANJA_MIN, HUE_LARANJA_MAX = 5, 25
SAT_MIN_BUSCA = 150
VAL_MIN_BUSCA = 60

# Retângulo da "janela" central, em FRAÇÃO do tamanho da captura (não
# pixels fixos, para não depender da resolução) -- exclui a barra de
# mensagens/INFO no topo, a lista lateral de alvos à esquerda, e o
# dashboard (radar, fuel, heatsinks) em baixo. Calibrado a partir de
# temp/Mm.png e temp/Ls.png (2050x1238): topo ~320/1238=0.26,
# base ~820/1238=0.66, esquerda ~550/2050=0.27. Ainda por validar
# contra mais variações de FOV/resolução.
JANELA_TOPO_FRAC = 0.26
JANELA_BASE_FRAC = 0.66
JANELA_ESQUERDA_FRAC = 0.27

# Dimensões plausíveis de um glifo À RESOLUÇÃO NATIVA (sem upscale) --
# medidas em componentes reais: dígitos ~15-18px de altura, o ponto
# decimal ~5-6px. área mínima baixa de propósito para não perder o
# ponto; o ruído residual é filtrado depois por não formar uma leitura
# válida (regex), não por aqui.
ALTURA_MIN_GLIFO_NATIVA = 4
ALTURA_MAX_GLIFO_NATIVA = 30
AREA_MIN_GLIFO_NATIVA = 12

TOLERANCIA_BASE_NATIVA = 3  # px nativos -- caracteres da mesma linha assentam na mesma base (y+h)


def localizar_linhas_candidatas(img_bgr: np.ndarray):
    """ Procura, dentro da janela central (exclui painéis fixos do HUD --
    ver JANELA_*_FRAC), blocos de texto verde OU laranja/âmbar agrupados
    por linha (mesma base y+h). Devolve uma lista de linhas, cada uma
    uma lista de (x, y, w, h, char, conf) com coordenadas relativas à
    imagem ORIGINAL (não à janela recortada nem ao espaço 8x usado
    internamente por _reconhecer_caractere -- o upscale é feito aqui
    por caractere, só quando necessário, para não ter de upscalar o
    ecrã inteiro). """
    alto, largo = img_bgr.shape[:2]
    y0 = int(alto * JANELA_TOPO_FRAC)
    y1 = int(alto * JANELA_BASE_FRAC)
    x0 = int(largo * JANELA_ESQUERDA_FRAC)
    janela = img_bgr[y0:y1, x0:largo]

    # Máscara híbrida: cor (matiz+saturação) AND brilho (threshold de
    # cinzentos) -- confirmado necessário em teste real (2026-09-17): a
    # máscara de cor sozinha dá contornos com formas ligeiramente
    # diferentes das dos templates (recortados com este mesmo threshold
    # combinado), o que baixa a confiança do reconhecimento para perto
    # mas abaixo do limite (0.30-0.55 em vez de >0.60) mesmo em
    # caracteres visualmente corretos. A dilatação ligeira (como no
    # pipeline de produção) volta a juntar o anti-aliasing dos traços.
    hsv = cv2.cvtColor(janela, cv2.COLOR_BGR2HSV)
    h_ch, s_ch, v_ch = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]
    cor_ok = (((h_ch >= HUE_VERDE_MIN) & (h_ch <= HUE_VERDE_MAX)) |
              ((h_ch >= HUE_LARANJA_MIN) & (h_ch <= HUE_LARANJA_MAX)))
    mask_cor = (cor_ok & (s_ch > SAT_MIN_BUSCA) & (v_ch > VAL_MIN_BUSCA)).astype(np.uint8) * 255
    mask_cor = cv2.dilate(mask_cor, np.ones((3, 3), np.uint8), iterations=1)
    gray = cv2.cvtColor(janela, cv2.COLOR_BGR2GRAY)
    _, gray_thresh = cv2.threshold(gray, 100, 255, cv2.THRESH_BINARY)
    mask = cv2.bitwise_and(mask_cor, gray_thresh)

    contornos, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    caixas = []
    for c in contornos:
        x, y, w, h = cv2.boundingRect(c)
        area = cv2.contourArea(c)
        if area < AREA_MIN_GLIFO_NATIVA or not (ALTURA_MIN_GLIFO_NATIVA <= h <= ALTURA_MAX_GLIFO_NATIVA):
            continue
        caixas.append((x + x0, y + y0, w, h))  # de volta às coordenadas da imagem original

    grupos = {}
    for (x, y, w, h) in caixas:
        base = y + h
        chave = next((g for g in grupos if abs(g - base) <= TOLERANCIA_BASE_NATIVA), None)
        if chave is None:
            chave = base
            grupos[chave] = []
        grupos[chave].append((x, y, w, h))

    linhas = []
    for base, caixas_linha in sorted(grupos.items()):
        caixas_linha.sort(key=lambda b: b[0])
        candidatos = []
        for (x, y, w, h) in caixas_linha:
            # (x, y) já foram convertidos para coordenadas da imagem
            # original -- "mask" é só da janela recortada, por isso
            # subtrai o deslocamento (x0, y0) de volta ao indexar.
            blob_nativo = mask[y - y0:y - y0 + h, x - x0:x - x0 + w]
            blob_big = cv2.resize(blob_nativo, (w * FATOR_UPSCALE_DIGITOS, h * FATOR_UPSCALE_DIGITOS),
                                   interpolation=cv2.INTER_NEAREST)
            char, conf = reconhecer_caractere(blob_big)
            candidatos.append((x, y, w, h, char, conf))
        linhas.append(candidatos)
    return linhas, mask


def montar_leitura_linha(candidatos):
    """ Junta uma linha de candidatos num texto de melhor esforço (não
    desiste ao primeiro caractere não reconhecido, ao contrário de
    ler_distancia_hud() em produção -- aqui interessa ver tudo).
    Devolve (texto, valor_ou_none). """
    texto = ""
    completo = True
    for (x, y, w, h, char, conf) in candidatos:
        if char is not None:
            texto += char
            continue
        w_big, h_big = w * FATOR_UPSCALE_DIGITOS, h * FATOR_UPSCALE_DIGITOS
        if w_big <= 40 and 0.6 <= (w_big / h_big) <= 1.6:
            texto += "."
            continue
        texto += "?"
        completo = False

    if not completo or not texto:
        return texto, None
    m = re.match(r"^(\d+)\.(\d+)(km|Mm|Ls)$", texto)
    if not m:
        return texto, None
    valor = float(f"{m.group(1)}.{m.group(2)}") * MAPA_UNIDADE_KM[m.group(3)]
    return texto, valor


def desenhar_overlay(img_bgr, linhas, leituras):
    anotado = img_bgr.copy()
    for candidatos, (texto, valor) in zip(linhas, leituras):
        # Linhas com >=3 caixas alinhadas na mesma base já são um sinal
        # forte de texto real (ruído de fundo raramente alinha 3+ manchas
        # na mesma base) -- mostra-as mesmo que NENHUM caractere tenha
        # sido reconhecido com confiança, para a pessoa poder ver "está
        # ali algo parecido com texto" mesmo quando o reconhecimento
        # falhou por completo (confirmado em teste real, 2026-09-18: a
        # cor sozinha não chega quando o fundo tem tons parecidos com o
        # texto -- ver nota em HUE_LARANJA_MIN/MAX -- mas a linha continua
        # visível por ter várias caixas alinhadas).
        if len(candidatos) < 3:
            continue  # linha pouco interessante (ruído isolado) -- não polui a visualização
        for (x, y, w, h, char, conf) in candidatos:
            cor = (0, 255, 0) if char is not None else (0, 0, 255)
            cv2.rectangle(anotado, (x, y), (x + w, y + h), cor, 1)
        x0 = candidatos[0][0]
        y0 = min(c[1] for c in candidatos)
        cor_linha = (0, 255, 255) if valor is not None else (0, 128, 255)
        rotulo = f"'{texto}' -> {valor if valor is not None else '?'}"
        cv2.putText(anotado, rotulo, (x0, max(0, y0 - 8)), cv2.FONT_HERSHEY_SIMPLEX, 0.6, cor_linha, 2)
    return anotado


def propor_nome(texto, valor):
    if valor is not None:
        return texto
    if texto:
        return "incompleto_" + texto.replace("?", "X")
    return "ilegivel"


def sanitizar_nome(nome):
    permitido = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")
    limpo = "".join(c for c in nome.strip() if c in permitido)
    return limpo or "sem_nome"


def caminho_sem_colisao(pasta: Path, nome: str) -> Path:
    candidato = pasta / f"{nome}.png"
    if not candidato.exists():
        return candidato
    i = 2
    while (pasta / f"{nome}_{i}.png").exists():
        i += 1
    return pasta / f"{nome}_{i}.png"


def escolher_linha_para_gravar(linhas, leituras):
    """ Entre as linhas com leitura válida, escolhe a mais alta no ecrã
    (menor y) -- a etiqueta presa ao retículo tende a estar acima de
    listas/paineis. Se não houver nenhuma válida, escolhe pela linha
    mais "parecida com texto": primeiro por caracteres reconhecidos,
    depois por número de caixas alinhadas na mesma base -- confirmado
    em teste real (2026-09-18) que ruído de fundo colorido raramente
    alinha 3+ manchas na mesma base, mesmo quando nenhuma delas chega a
    ser reconhecida com confiança (fundo com tom parecido ao do texto);
    mostrar essa linha como "melhor tentativa" continua a ser mais útil
    à pessoa do que devolver nada. """
    validas = [(c, t, v) for c, (t, v) in zip(linhas, leituras) if v is not None]
    if validas:
        return min(validas, key=lambda item: min(b[1] for b in item[0]))
    candidatas = [(c, t, v) for c, (t, v) in zip(linhas, leituras) if len(c) >= 3]
    if not candidatas:
        return None
    def pontuacao(item):
        # Ter um "." geometricamente plausível pesa mais do que o número
        # de caracteres reconhecidos -- confirmado necessário em teste
        # real (2026-09-18): quando o reconhecimento falha por completo
        # nas duas linhas (nome do alvo E valor da distância, ambas sem
        # templates que cubram letras do nome), contar só caixas/chars
        # reconhecidos empata as duas e a linha errada ("FUTEN
        # SPACEPORT") ganha por vir primeiro no ecrã. Um nome de alvo
        # nunca tem ponto decimal; uma distância tem sempre.
        c, t, v = item
        tem_ponto = "." in t
        reconhecidos = sum(1 for b in c if b[4] is not None)
        return (tem_ponto, reconhecidos, len(c))
    return max(candidatas, key=pontuacao)


def gravar(img_bgr_bruto, linhas, leituras):
    escolha = escolher_linha_para_gravar(linhas, leituras)
    if escolha is None:
        print("\n[AVISO] Nenhuma linha candidata neste frame -- nada para gravar.")
        return
    candidatos, texto, valor = escolha

    x_min = min(b[0] for b in candidatos)
    y_min = min(b[1] for b in candidatos)
    x_max = max(b[0] + b[2] for b in candidatos)
    y_max = max(b[1] + b[3] for b in candidatos)
    margem = 4
    recorte = img_bgr_bruto[max(0, y_min - margem):y_max + margem, max(0, x_min - margem):x_max + margem]

    proposto = propor_nome(texto, valor)
    print()
    print(f"[LEITURA] texto='{texto}'  valor={valor}  (região x={x_min}-{x_max} y={y_min}-{y_max})")
    print(f"[PROPOSTA] nome sugerido: '{proposto}'")
    resposta = input("  ENTER para aceitar, ou escreve o valor real (ex: 9.87km): ").strip()
    nome_final = sanitizar_nome(resposta) if resposta else sanitizar_nome(proposto)

    caminho_captura = caminho_sem_colisao(PASTA_CAPTURAS, nome_final)
    cv2.imwrite(str(caminho_captura), recorte)

    timestamp = time.strftime("%Y%m%d_%H%M%S")
    anotado = desenhar_overlay(img_bgr_bruto, linhas, leituras)
    caminho_vis = PASTA_VISUALIZACOES / f"{timestamp}_{nome_final}.png"
    cv2.imwrite(str(caminho_vis), anotado)

    print(f"[GRAVADO] {caminho_captura}")
    print(f"[GRAVADO] {caminho_vis}")


def main():
    print("=" * 60)
    print("DEBUG LIVE: Leitura de distância HUD (busca dinâmica, coletor de dataset)")
    print("=" * 60)
    print(f"Templates carregados: {sorted(DIGITOS_HUD.keys())}")
    print()
    print("Janela ao vivo -- procura o texto verde em toda a área capturada,")
    print("não assume posição fixa (a etiqueta segue o alvo no ecrã).")
    print("Teclas (com a janela em foco): 's' propõe nome e grava | 'q' ou ESC sai.")
    print()

    janela = "Debug HUD Distancia (busca dinamica, live)"

    with mss.mss() as sct:
        try:
            monitor_jogo = sct.monitors[1]
        except Exception:
            monitor_jogo = sct.monitors[0]

    cv2.namedWindow(janela, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(janela, 1000, 600)
    cv2.moveWindow(janela, SCREEN_WIDTH, 0)
    cv2.setWindowProperty(janela, cv2.WND_PROP_TOPMOST, 1)

    with mss.mss() as sct:
        while True:
            img_bgra = np.array(sct.grab(monitor_jogo))
            img_bgr = cv2.cvtColor(img_bgra, cv2.COLOR_BGRA2BGR)

            linhas, _ = localizar_linhas_candidatas(img_bgr)
            leituras = [montar_leitura_linha(c) for c in linhas]
            anotado = desenhar_overlay(img_bgr, linhas, leituras)

            cv2.imshow(janela, anotado)
            tecla = cv2.waitKey(REFRESCO_MS) & 0xFF
            if tecla in (ord('q'), 27):
                break
            if tecla == ord('s'):
                gravar(img_bgr, linhas, leituras)

    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
