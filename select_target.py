import os
import sys
import time
import glob
import json
import logging
import cv2
import numpy as np
import pyttsx3
from infra_bridge import pydirectinput, gw, winsound, mss, ED_STATUS_FILE, print_ts as print
import time

# ==========================================
# 0. LOGGING E INFRAESTRUTURA
# ==========================================
diretorio_atual = os.path.dirname(os.path.abspath(__file__))
pasta_logs = os.path.join(diretorio_atual, "logs")
os.makedirs(pasta_logs, exist_ok=True)
# Última captura de procurar_template(), sobrescrita a cada chamada -- dá
# evidência forense de qualquer falha sem depender de VISUAL_DEBUG (mesmo
# padrão do undocking.py, que já foi decisivo para diagnosticar os bugs do
# menu estação-vs-carrier).
log_test = os.path.join(pasta_logs, "select_target_test.png")

# Logger proprio (nao usa logging.basicConfig -- com varios scripts no mesmo
# processo, so o primeiro basicConfig chamado ganha, e todos os outros ficam
# com o prefixo errado no log partilhado).
_logger = logging.getLogger("select_target")
_logger.setLevel(logging.ERROR)
if not _logger.handlers:
    _fh = logging.FileHandler(os.path.join(pasta_logs, "r2d2_combined.log"), encoding='utf-8')
    _fh.setFormatter(logging.Formatter('%(asctime)s - [SELECT_TARGET] - %(levelname)s - %(message)s'))
    _logger.addHandler(_fh)
    _logger.propagate = False

def abortar_com_erro(mensagem):
    """ Regista o erro no log e dispara exit code 1 para o Orquestrador intercetar """
    print(f"\n[FATAL] {mensagem}")
    _logger.error(mensagem)
    pydirectinput.press('backspace')
    sys.exit(1)

def ler_destino_telemetria():
    """ Lê o campo Destination do Status.json -- só existe (e só reflete o
    nome certo) quando o jogo tem mesmo um destino de navegação trancado,
    independente do que os templates de "LOCK/UNLOCK DESTINATION" leem no
    ecrã. Fonte de verdade -- mesmo mecanismo do docking.py/ja_esta_atracada.
    Motivo: "LOCK DESTINATION" vs "UNLOCK DESTINATION" partilham glifos e
    cores ao ponto de a contaminação cruzada (~0.78-0.79 medida em produção,
    2026-09-12) cair em cima do próprio LOCK_THRESHOLD -- qualquer variação
    de compressão/anti-aliasing pode fazer o texto ERRADO passar no
    threshold. Já mordeu 2x (2026-08-19 Zahir, 2026-09-12 Futen), da segunda
    vez ao ponto de destrancar de verdade um alvo já correto numa reentrada.
    """
    try:
        with open(ED_STATUS_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
            return ((data.get("Destination") or {}).get("Name") or "").strip()
    except Exception:
        return ""

def ja_trancado_em(nome_confirma):
    """ nome_confirma vem em maiúsculas e por vezes embrulhado (ex: "CARRIER
    (ZAHIR W6G-26N)") -- comparação por substring cobre esse caso e o
    simples ("FUTEN SPACEPORT" == "FUTEN SPACEPORT"). """
    destino = ler_destino_telemetria().upper()
    return bool(destino) and destino in nome_confirma.upper()

NOME_JANELA = "Ocular do Bot - Navegacao"
VISUAL_DEBUG = False # Muda para False para esconder as janelas

def inicializar_infraestrutura():
    """ Foca no jogo (Ecrã 1) e envia o painel visual para o Ecrã 2 APENAS SE VISUAL_DEBUG FOR TRUE """
    print("[SISTEMA] A configurar foco no jogo...")
    
    focar_jogo_seguro()
    time.sleep(0.5)
            
    if VISUAL_DEBUG:
        print("[SISTEMA] Modo Debug Ativo: A configurar janelas...")
        cv2.namedWindow(NOME_JANELA, cv2.WINDOW_NORMAL)
        with mss.mss() as sct:
            monitores = sct.monitors
            if len(monitores) > 2:
                ecra_secundario = monitores[2]
                cv2.moveWindow(NOME_JANELA, ecra_secundario["left"] + 50, ecra_secundario["top"] + 50)
            else:
                cv2.moveWindow(NOME_JANELA, 50, 50)
            cv2.setWindowProperty(NOME_JANELA, cv2.WND_PROP_TOPMOST, 1)

def focar_jogo_seguro():
    """ Foca o Elite Dangerous a nível de Sistema Operativo, sem enviar cliques de rato """
    print("[SISTEMA] A focar o Elite Dangerous via Windows API...")
    try:
        # Procura a janela pelo título (no Elite geralmente é "Elite - Dangerous (CLIENT)")
        janelas = gw.getWindowsWithTitle("Elite - Dangerous (CLIENT)")
        
        if janelas:
            janela_elite = janelas[0]
            # Traz a janela para a frente
            janela_elite.activate() 
            time.sleep(0.5)
            print("[OK] Jogo focado com sucesso e em segurança.")
            return True
        else:
            print("[ERRO] Janela do Elite Dangerous não encontrada!")
            return False
            
    except Exception as e:
        print(f"[AVISO] Falha ao forçar foco via OS: {e}")
        return False

# ==========================================
# 1. SETUP E ÁREAS
# ==========================================
MONITOR_PANEL = {"top": 200, "left": 30, "width": 1000, "height": 800}
from infra_bridge import ED_LOG_DIR
LOG_DIR = ED_LOG_DIR

pasta_imagens = os.path.join(diretorio_atual, 'images')

templates_nomes = {
    'nav_tab': 'NAVIGATION_SELECTED.png',
    'carrier': 'FLEET_CARRIER_NAME.png',
    'station': 'STATION.png',
    'station_alt': 'STATION1.png',
    # Texto do próprio botão de ação no ecrã de detalhe ("LOCK DESTINATION"
    # / "UNLOCK DESTINATION") -- substitui LOCKED_DESTINATION.png /
    # UNLOCKED_DESTINATION.png (arquivados com prefixo x), que verificavam
    # a região larga do painel e podiam apanhar o cadeado de OUTRA linha
    # (ex: a estação anterior, ainda trancada, visível por trás do cartão
    # semi-transparente) e concluir "já trancado" sem nunca ter trancado o
    # alvo certo -- bug real, confirmado em produção (2026-08-19 18:59: o
    # Zahir nunca foi trancado, o Futen do ciclo anterior continuou como
    # destino real, a nave ia descolar com 35 unidades por vender). Estes
    # só existem dentro do cartão aberto -- não podem confundir-se com outra
    # linha.
    'locked': 'UNLOCK_DESTINATION_BUTTON.png',
    'unlocked': 'LOCK_DESTINATION_BUTTON.png',
    # Título do ecrã de detalhe (ícone + nome), calibrado diretamente do jogo
    # na nave atual -- ver área1.png/área2.png. Aparece assim que se abre o
    # detalhe do alvo, ANTES de qualquer lock/unlock, por isso serve para
    # confirmar o nome cedo (ver marcar_destino_dinamico).
    'confirma_carrier': 'carrier_destination_confirm.png',
    'confirma_station': 'futen_destination_check.png',
    # Fallback da confirmação pós-lock: o jogo às vezes fecha o cartão de
    # detalhe sozinho a seguir ao 'space' que tranca, voltando à lista --
    # nesse caso o texto do botão (acima) já não está visível, mas o nome
    # aparece entre setas "< NOME >" (sinal fiável confirmado em jogo:
    # setas = trancado). Bug real, confirmado em produção (2026-08-19
    # 19:37): Futen estava mesmo trancado (confirmado por Status.json e por
    # esta própria lista) mas o check antigo (só cartão) abortou 3x a
    # achar que tinha falhado. Variante de carrier recalibrada em
    # 2026-08-19 20:14 (mesmo bug, desta vez com o Zahir).
    'confirma_lista_station': 'futen_destination_confirm_lista.png',
    'confirma_lista_carrier': 'zahir_destination_confirm_lista.png'
}

templates = {}
try:
    for chave, nome_arq in templates_nomes.items():
        caminho = os.path.join(pasta_imagens, nome_arq)
        img = cv2.imread(caminho, cv2.IMREAD_COLOR)
        if img is None: raise FileNotFoundError(f"Falta imagem: {caminho}")
        templates[chave] = img
    print(f"[SISTEMA] Módulo Dinâmico de Navegação Blindado carregado.")
except Exception as e:
    abortar_com_erro(f"Falha ao carregar assinaturas visuais: {e}")

# --- Voz ---
engine = pyttsx3.init()
def falar(texto):
    print(f"[VOZ] {texto}")
    engine.say(texto)
    engine.runAndWait()

# ==========================================
# 2. MOTOR DE CONTEXTO (LOGS DO ELITE)
# ==========================================
def obter_alvo_contextual_log():
    """ 
    Lê o Journal e decide o destino com base no ESTADO ATUAL (docked ou undocked).
    Retorna o DESTINO ALVO para marcar.
    """
    try:
        list_of_files = glob.glob(os.path.join(LOG_DIR, 'Journal.*.log'))
        if not list_of_files: 
            return "carrier" # Fallback de segurança
            
        latest_log = max(list_of_files, key=os.path.getctime)
        
        with open(latest_log, 'r', encoding='utf-8') as f:
            linhas = f.readlines()
            # Procurar o último evento para saber o estado atual
            ultimo_evento = None
            for linha in reversed(linhas):
                try:
                    data = json.loads(linha)
                    if 'StationType' in data:
                        ultimo_evento = data
                        # Prioridade: Docked/Undocked > Location > fallback
                        if 'Docked' in data.get('event', '') or 'Undocked' in data.get('event', ''):
                            break  # Usa o Docked/Undocked mais recente
                        # Event Location também indica onde está
                        elif data.get('event') == 'Location':
                            break  # Usa Location como fallback
                except json.JSONDecodeError:
                    continue
            
            # Verifica o evento encontrado
            if ultimo_evento and ultimo_evento.get('event') in ['Docked', 'Undocked', 'Location']:
                tipo = ultimo_evento['StationType']
                evento = ultimo_evento['event']
                
                print(f"[CONTEXTO] Localização: {evento} - Tipo: {tipo}")
                
                if evento == 'Undocked':
                    # Descolou recentemente: quer ir ao OPPOSTO
                    if tipo == 'FleetCarrier':
                        print("[CONTEXTO] Descolou de Carrier. Destino: ESTAÇÃO.")
                        return "station"
                    else:
                        print("[CONTEXTO] Descolou de Station. Destino: CARRIER.")
                        return "carrier"
                elif evento == 'Docked':
                    # Estacionado: quer ir ao OPPOSTO para descolar
                    if tipo == 'FleetCarrier':
                        print("[CONTEXTO] Dentro do carrier. Destino para descolar: ESTAÇÃO.")
                        return "station"
                    else:
                        print("[CONTEXTO] Dentro da estação. Destino para descolar: CARRIER.")
                        return "carrier"
                elif evento == 'Location':
                    # Último evento Location: assume que está no tipo
                    # Se Location Carrier -> descolar para estação
                    # Se Location Station -> descolar para carrier
                    if tipo == 'FleetCarrier':
                        print("[CONTEXTO] Location Carrier. Quer descolar e ir à ESTAÇÃO.")
                        return "station"
                    else:
                        print("[CONTEXTO] Location Station. Quer descolar e ir ao CARRIER.")
                        return "carrier"
            
            # Fallback total
            print("[CONTEXTO] Sem eventos válidos. A assumir: CARRIER.")
            return "carrier"
                    
    except Exception as e:
        print(f"[AVISO] Falha ao ler o log para contexto dinâmico ({e}).")
        
    print("[CONTEXTO] Sem eventos válidos. A assumir: CARRIER.")
    return "carrier"

# ==========================================
# 3. MOTOR DE VISÃO
# ==========================================
def procurar_template(template, nome_label, monitor, threshold=0.80):
    """
    'template' aceita:
      - um único array (avaliado contra 'threshold')
      - uma lista de tuplos (array, threshold_proprio) - cada variante (ex: STATION/STATION1)
        é avaliada de forma independente contra a SUA própria percentagem de match.
        Basta uma das variantes passar no seu threshold para dar 'encontrou=True'
        (só uma delas estará presente no ecrã de cada vez).
    """
    if isinstance(template, list):
        variantes = [(t, th) for t, th in template if t is not None]
    else:
        variantes = [(template, threshold)] if template is not None else []
    if not variantes: return False

    with mss.mss() as sct:
        img_bgra = np.array(sct.grab(monitor))
        img_bgr = cv2.cvtColor(img_bgra, cv2.COLOR_BGRA2BGR)
        cv2.imwrite(log_test, img_bgr)

        encontrou = False
        melhor_val, melhor_loc, melhor_tmpl, melhor_th = -1.0, (0, 0), variantes[0][0], variantes[0][1]
        for t, th in variantes:
            resultado = cv2.matchTemplate(img_bgr, t, cv2.TM_CCOEFF_NORMED)
            _, max_val, _, max_loc = cv2.minMaxLoc(resultado)
            if max_val >= th:
                encontrou = True
            if max_val > melhor_val:
                melhor_val, melhor_loc, melhor_tmpl, melhor_th = max_val, max_loc, t, th

        if VISUAL_DEBUG:
            cor = (0, 255, 0) if encontrou else (0, 0, 255)
            if encontrou:
                h, w = melhor_tmpl.shape[:2]
                cv2.rectangle(img_bgr, melhor_loc, (melhor_loc[0] + w, melhor_loc[1] + h), cor, 2)

            cv2.rectangle(img_bgr, (5, 5), (350, 80), (0, 0, 0), -1)
            cv2.putText(img_bgr, f"Alvo: {nome_label}", (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
            cv2.putText(img_bgr, f"Match: {melhor_val:.2f} / {melhor_th:.2f}", (10, 65), cv2.FONT_HERSHEY_SIMPLEX, 0.8, cor, 2)

            cv2.imshow(NOME_JANELA, img_bgr)
            cv2.waitKey(1)

        print(f"[TRACE] {nome_label}: match={melhor_val:.3f} threshold={melhor_th:.2f} loc={melhor_loc} -> {'OK' if encontrou else '--'}")
        return encontrou

# ==========================================
# 4. LÓGICA DE MARCAÇÃO INTELIGENTE
# ==========================================
def marcar_destino_dinamico():
    tipo_alvo = obter_alvo_contextual_log()
    label_alvo = "STATION" if tipo_alvo == "station" else "CARRIER"

    # Station tem duas variantes visuais (normal / _alt), cada uma com o seu threshold.
    # Carrier medido ao vivo em jogo (07/2026): match consistente 0.83-0.846, ruido
    # de fundo fica em 0.32-0.36 -- 0.85 estava a falhar por pouco, baixado para
    # dar margem de seguranca real sem colidir com o ruido.
    if tipo_alvo == "station":
        template_alvo = [(templates['station'], 0.75), (templates['station_alt'], 0.75)]
        template_confirma, nome_confirma = templates['confirma_station'], "FUTEN SPACEPORT"
        template_confirma_lista = templates['confirma_lista_station']
    else:
        template_alvo = [(templates['carrier'], 0.78)]
        template_confirma, nome_confirma = templates['confirma_carrier'], "CARRIER (ZAHIR W6G-26N)"
        template_confirma_lista = templates['confirma_lista_carrier']

    print(f"\n>>> FASE: Marcar Destino ({label_alvo})...")
    pydirectinput.press('1')
    time.sleep(1.2)

    # Watchdog: Encontrar a aba NAVIGATION
    # Às vezes uma luz (sol/estação) bate exatamente em cima da aba e oclui
    # a deteção momentaneamente -- watchdog continuo de 6 minutos em vez de
    # desistir cedo, para dar tempo a luz mudar de posição.
    nav_found = False
    timeout_nav = time.time() + 360  # 6 minutos
    while time.time() < timeout_nav:
        # Threshold NAV TAB medido ao vivo em jogo: match=0.8409 -> 0.80 com margem de segurança
        if procurar_template(templates['nav_tab'], "NAV TAB", MONITOR_PANEL, 0.61):
            nav_found = True
            break
        pydirectinput.press('q'); time.sleep(0.5)

    if not nav_found:
        abortar_com_erro("Falha ao focar na aba de navegação do painel esquerdo após 6 minutos de tentativas.")

    pydirectinput.press('d'); time.sleep(0.5)

    # Varre a lista à procura de um candidato (ícone genérico STATION/CARRIER
    # -- não distingue QUAL estação/carrier, e em sistemas com dezenas de
    # carriers pode calhar num diferente do pretendido) e confirma o NOME
    # assim que o ecrã de detalhe abre, ANTES de mexer no lock -- não vale a
    # pena trancar um alvo que nem é o certo. Se o nome não bater, volta à
    # lista e continua a varrer para o candidato seguinte; até 3 candidatos
    # verificados (na prática o certo costuma aparecer à 2ª ou 3ª).
    MAX_TENTATIVAS_NOME = 3
    tentativas_nome = 0
    achou_nome_certo = False

    for i in range(40):
        if not procurar_template(template_alvo, label_alvo, MONITOR_PANEL):
            pydirectinput.press('s'); time.sleep(0.4)
            continue

        print(f"\n>>> FASE: ACHOU ({label_alvo}) -- a confirmar nome antes do lock...")
        time.sleep(2.4)  # Dá tempo ao ecrã de detalhe pop-up para renderizar
        pydirectinput.press('space')
        time.sleep(1.0)

        if procurar_template(template_confirma, f"CONFIRMA {nome_confirma}", MONITOR_PANEL, 0.80):
            print(f"[OK] Nome confirmado ({nome_confirma}).")
            achou_nome_certo = True
            break

        tentativas_nome += 1
        msg = (f"Candidato para {label_alvo} não confere com '{nome_confirma}' "
               f"(tentativa {tentativas_nome}/{MAX_TENTATIVAS_NOME}) -- provavelmente outro "
               f"{label_alvo.lower()} com ícone parecido.")
        print(f"[AVISO] {msg}")
        _logger.error(msg)

        if tentativas_nome >= MAX_TENTATIVAS_NOME:
            break

        pydirectinput.press('backspace'); time.sleep(0.8)  # sai do ecrã de detalhe errado
        pydirectinput.press('s'); time.sleep(0.4)  # avança para o candidato seguinte

    if not achou_nome_certo:
        abortar_com_erro(f"Não foi possível confirmar o nome certo ('{nome_confirma}') para {label_alvo} "
                          f"em {MAX_TENTATIVAS_NOME} tentativas -- possível ambiguidade entre vários "
                          f"candidatos com ícone parecido. Intervenção manual necessária.")

    # Verificação de Bloqueio (Lock) -- só chega aqui com o nome já confirmado
    # certo. A telemetria (Status.json -> Destination.Name) é consultada
    # ANTES de decidir premir 'space': é a fonte de verdade, e evita depender
    # dos templates "LOCK DESTINATION" / "UNLOCK DESTINATION", que partilham
    # glifos/cores ao ponto de a contaminação cruzada (~0.78-0.79, medida em
    # produção 2026-09-12) cair em cima do próprio LOCK_THRESHOLD. Sem este
    # guard, uma reentrada (script já trancou, mas abortou por não conseguir
    # CONFIRMAR visualmente) podia interpretar "já trancado" como "por
    # trancar" e premir 'space' de novo -- destrancando de facto um alvo já
    # correto. Bug real, confirmado em produção 2x (2026-08-19 Zahir,
    # 2026-09-12 Futen).
    LOCK_THRESHOLD = 0.78
    if ja_trancado_em(nome_confirma):
        print(f"[LOG] Destino já trancado confirmado por telemetria (Status.json: '{ler_destino_telemetria()}').")
        falar(f"{label_alvo} already locked.")
    elif procurar_template(templates['unlocked'], "UNLOCKED", MONITOR_PANEL, LOCK_THRESHOLD):
        pydirectinput.press('space')

        # Espera a telemetria refletir o lock (fonte de verdade) em vez de
        # confiar num único frame de UI logo a seguir ao 'space' -- o cartão
        # pode fechar-se sozinho e a decoração visual de confirmação atrasar-
        # se (ver histórico acima). Até 3s, a cada 0.5s.
        confirmado_pos_lock = False
        for _tentativa in range(6):
            time.sleep(0.5)
            if ja_trancado_em(nome_confirma):
                confirmado_pos_lock = True
                break

        if not confirmado_pos_lock:
            # Fallback visual -- só chega aqui se a telemetria não confirmou
            # em 3s (ex: campo Destination ainda não populado nesta versão
            # do jogo para este tipo de alvo).
            confirmado_pos_lock = procurar_template(templates['locked'], "LOCKED (POS-LOCK, cartao)", MONITOR_PANEL, LOCK_THRESHOLD)
            if not confirmado_pos_lock and template_confirma_lista is not None:
                confirmado_pos_lock = procurar_template(template_confirma_lista, "LOCKED (POS-LOCK, lista)", MONITOR_PANEL, LOCK_THRESHOLD)

        if not confirmado_pos_lock:
            abortar_com_erro(f"O 'space' para trancar {nome_confirma} não teve efeito -- nem a telemetria "
                              f"(Status.json) nem o cartão nem a lista confirmam o lock. Intervenção manual necessária.")
        falar(f"{label_alvo} destination locked.")
    elif procurar_template(templates['locked'], "LOCKED", MONITOR_PANEL, LOCK_THRESHOLD):
        print("[LOG] Destino já estava trancado.")
        falar(f"{label_alvo} already locked.")
    else:
        # Não lança erro fatal aqui porque o jogo às vezes ofusca o botão com partículas holográficas
        print(f"[AVISO] Não foi possível validar visualmente o Lock no {label_alvo}. Assumindo sucesso cego.")
        pydirectinput.press('space')

    pydirectinput.press('backspace') # Fecha o painel (ou sobe um nível)
    time.sleep(1.0)
    return True

def executar():
    inicializar_infraestrutura()

    print("O R2D2 assume os comandos em 1 segundos...")
    time.sleep(1)

    marcar_destino_dinamico()
    pydirectinput.press('backspace')

    if VISUAL_DEBUG:
        cv2.destroyAllWindows()

if __name__ == "__main__":
    executar()
