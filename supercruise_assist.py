#!/usr/bin/env python3
"""
Supercruise Assist - Módulo Unificado (Mecânica Ótica + Telemetria)
Elite Dangerous Automation
"""

import os
# Força o Qt (janelas de debug do cv2) a usar XCB em vez de tentar
# "wayland" primeiro -- nesta sessão o plugin wayland não existe
# (qt.qpa.plugin: Could not find...) e a tentativa/fallback demora o
# suficiente para atrapalhar a animação de fecho do painel do jogo logo a
# seguir a engatar_assistencia_menu(), deixando a bússola/HUD a ler o
# ecrã ainda a meio da transição. XCB funciona bem via XWayland no KDE.
# Só define se ainda não estiver definida, para não sobrepor uma escolha
# explícita do utilizador/ambiente.
os.environ.setdefault('QT_QPA_PLATFORM', 'xcb')

import sys
import glob
import json
import time
import logging
import cv2
import numpy as np
import pyttsx3
from infra_bridge import pydirectinput, gw, winsound, mss, leg_esta_limpa, print_ts as print
import time

# ==========================================
# 0. LOGGING E INFRAESTRUTURA
# ==========================================
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
LOGS_DIR = os.path.join(PROJECT_DIR, "logs")
IMAGES_DIR = os.path.join(PROJECT_DIR, "images")
os.makedirs(LOGS_DIR, exist_ok=True)
# Última captura de procurar_template(), sobrescrita a cada chamada -- dá
# evidência forense de qualquer falha sem depender de VISUAL_DEBUG (mesmo
# padrão dos outros scripts).
LOG_TEST = os.path.join(LOGS_DIR, "supercruise_test.png")
# Mesmo padrão, mas para a área da bússola (_alinhar_com_olho) -- inclui o
# marcador de CX_NEUTRO/CY_NEUTRO desenhado por cima, na mesma resolução e
# referencial de pixels que o bot realmente usa (mss.grab), para comparar
# calibração sem depender de screenshots manuais tirados noutra escala/com
# decorações da janela, que não mapeiam 1:1 para estas coordenadas.
LOG_BUSSOLA = os.path.join(LOGS_DIR, "supercruise_bussola.png")
# Snapshot do exato momento em que o alinhamento é dado como confirmado --
# diferente do LOG_BUSSOLA (sobrescrito a cada iteração seguinte, pode já
# não corresponder ao instante da decisão) -- ver _gravar_snapshot_alinhado
# dentro de _alinhar_com_olho().
LOG_ALINHADO = os.path.join(LOGS_DIR, "supercruise_alinhado.png")

# Logger proprio (nao usa logging.basicConfig -- com varios scripts no mesmo
# processo, so o primeiro basicConfig chamado ganha, e todos os outros ficam
# com o prefixo errado no log partilhado).
_logger = logging.getLogger("supercruise")
_logger.setLevel(logging.ERROR)
if not _logger.handlers:
    _fh = logging.FileHandler(os.path.join(LOGS_DIR, "r2d2_combined.log"), encoding='utf-8')
    _fh.setFormatter(logging.Formatter('%(asctime)s - [SUPERCRUISE] - %(levelname)s - %(message)s'))
    _logger.addHandler(_fh)
    _logger.propagate = False

def _capturar_screenshot_erro(mensagem):
    """ Grava automaticamente o ecrã inteiro do jogo no momento do erro --
    substitui o processo manual (utilizador a tirar print screen à mão,
    que além de lento ainda vem com decorações de janela/escala diferente
    da que o bot usa -- ver LOG_BUSSOLA para o mesmo problema resolvido
    para a área da bússola). Nunca pode impedir o abort -- corre nun
    try/except que só regista a falha e continua. """
    try:
        with mss.mss() as sct:
            try: monitor_jogo = sct.monitors[1]
            except: monitor_jogo = sct.monitors[0]
            img = cv2.cvtColor(np.array(sct.grab(monitor_jogo)), cv2.COLOR_BGRA2BGR)
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        caminho = os.path.join(LOGS_DIR, f"erro_{timestamp}.png")
        cv2.imwrite(caminho, img)
        print(f"[FATAL] Screenshot do erro gravado em {caminho}")
    except Exception as e:
        print(f"[AVISO] Falha ao gravar screenshot do erro: {e}")

def abortar_com_erro(mensagem):
    print(f"\n[FATAL] {mensagem}")
    _logger.error(mensagem)
    _capturar_screenshot_erro(mensagem)
    sys.exit(1)

# Voz
engine = pyttsx3.init()
def falar(texto):
    print(f"[VOZ] {texto}")
    engine.say(texto)
    engine.runAndWait()

NOME_JANELA = "Ocular do Bot - Supercruise"
VISUAL_DEBUG = False

def inicializar_infraestrutura():
    print("[SISTEMA] A configurar foco no jogo...")
    
    focar_jogo_seguro()
    time.sleep(0.5)
            
    if VISUAL_DEBUG:
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
# 1. SETUP (TEMPLATES E TELEMETRIA)
# ==========================================
MONITOR_CENTER = {"top": 100, "left": 400, "width": 1100, "height": 800}
MONITOR_PANEL = {"top": 200, "left": 50, "width": 1000, "height": 1200}

STATUS_FLAGS = {
    "SUPERCRUISE": 0x10,
    "FSD_MASS_LOCKED": 0x10000,
    "FSD_CHARGING": 0x20000,
    "HARDPOINTS_DEPLOYED": 0x40,
    "INTERDICTION": 0x800000,
}

TEMPLATES_NOMES = {
    'nav_tab': 'NAVIGATION_SELECTED.png',
    'charging': 'CHARGING.png',
    'assist_active': 'SUPERCRUISE_ASSIST_ACTIVE.png',
    # Sequência de banners de transição, do momento em que o alvo é
    # apanhado até assentar no tamanho normal (assist_active) -- ver
    # assist_esta_ativo() / _alinhar_com_olho().
    'assist_active_dbl': 'assist-dbl-active.png',  # banner fantasma/duplicado
    'assist_active_big': 'assist-active-big.png',  # banner grande/em zoom
    'align_warning': 'SUPERCRUISE_ASSIST_INACTIVE.png',
    'throttle_up': 'THROTTLE_UP.png',
    # Confirmação do alvo (mesmos templates do select_target.py) --
    # ver engatar_assistencia_menu().
    'confirma_carrier': 'carrier_destination_confirm.png',
    'confirma_station': 'futen_destination_check.png'
}

templates = {}
try:
    from infra_bridge import ED_STATUS_FILE, ED_LOG_DIR
    STATUS_FILE = ED_STATUS_FILE
    LOG_DIR = ED_LOG_DIR
    for chave, nome_arq in TEMPLATES_NOMES.items():
        caminho = os.path.join(IMAGES_DIR, nome_arq)
        img = cv2.imread(caminho, cv2.IMREAD_COLOR)
        if img is None: raise FileNotFoundError(f"Falta imagem: {caminho}")
        templates[chave] = img
    print("[SISTEMA] Módulo Unified Supercruise carregado.")
except Exception as e:
    abortar_com_erro(f"Erro ao carregar templates visuais: {e}")

# ==========================================
# 2. MOTORES CORE (VISÃO E DADOS)
# ==========================================
def procurar_template(template, nome_label, monitor, threshold=0.75, log_trace=False):
    with mss.mss() as sct:
        img_bgra = np.array(sct.grab(monitor))
        img_bgr = cv2.cvtColor(img_bgra, cv2.COLOR_BGRA2BGR)
        cv2.imwrite(LOG_TEST, img_bgr)
        res = cv2.matchTemplate(img_bgr, template, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, max_loc = cv2.minMaxLoc(res)
        encontrou = max_val >= threshold

        if log_trace:
            print(f"[TRACE] {nome_label}: match={max_val:.3f} threshold={threshold:.2f} -> {'OK' if encontrou else '--'}")

        if VISUAL_DEBUG:
            cor = (0, 255, 0) if encontrou else (0, 0, 255)
            if encontrou:
                h, w = template.shape[:2]
                cv2.rectangle(img_bgr, max_loc, (max_loc[0] + w, max_loc[1] + h), cor, 2)
            cv2.putText(img_bgr, f"{nome_label}: {max_val:.2f}", (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, cor, 2)
            cv2.imshow(NOME_JANELA, img_bgr)
            cv2.waitKey(1)
            
        return encontrou

def assist_esta_ativo():
    """ Verifica o banner 'SUPERCRUISE ASSIST ACTIVE' -- normal (pequeno) OU
    a variante fantasma/duplicada (assist-dbl-active.png, recorte apertado
    tirado da própria transição, 124x106px -- substituiu uma tentativa
    anterior com um recorte grande e frágil, area-dbl-active.png, que nunca
    batia por não ter a mesma escala/enquadramento do template normal). """
    ativo = procurar_template(templates['assist_active'], "ASSIST ACTIVE", MONITOR_CENTER, 0.71, log_trace=True)
    if ativo:
        return True
    return procurar_template(templates['assist_active_dbl'], "ASSIST ACTIVE (fantasma)", MONITOR_CENTER, 0.71, log_trace=True)

def ler_telemetria():
    try:
        with open(STATUS_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
            return data.get("Flags", 0)
    except:
        return 0

def get_latest_log():
    list_of_files = glob.glob(os.path.join(LOG_DIR, 'Journal.*.log'))
    if not list_of_files: return None
    return max(list_of_files, key=os.path.getctime)

def confirmar_chegada_por_journal():
    """ Olha para trás no Journal a partir do SupercruiseExit mais recente e
    procura, a subir, qual dos dois marcos aparece primeiro: (a)
    SupercruiseDestinationDrop -- confirma que a queda foi mesmo a chegada
    ao alvo trancado -- ou (b) SupercruiseEntry / StartJump -- marca o
    início da sessão de supercruise atual sem nenhum DestinationDrop ter
    acontecido nela, ou seja, a queda foi involuntária (interdição, mass
    lock, ou qualquer outro motivo). Ignora tudo o resto pelo meio (Music,
    ReceiveText, etc.) -- só estes três eventos interessam.

    Validado contra o Journal real desta sessão (dezenas de viagens):
    SupercruiseDestinationDrop aparece sempre 1-2s antes do SupercruiseExit
    numa chegada legítima; nas quedas involuntárias observadas não há
    nenhum DestinationDrop entre a Entry e a Exit.

    Retorna:
      True  -- chegada legítima confirmada
      False -- queda não intencional confirmada
      None  -- ainda sem SupercruiseExit no Journal, ou Journal ilegível """
    latest_log = get_latest_log()
    if not latest_log:
        return None

    try:
        with open(latest_log, 'r', encoding='utf-8') as f:
            linhas = f.readlines()
    except Exception:
        return None

    eventos = []
    for linha in linhas:
        try:
            eventos.append(json.loads(linha))
        except json.JSONDecodeError:
            continue

    idx_exit = None
    for i in range(len(eventos) - 1, -1, -1):
        if eventos[i].get('event') == 'SupercruiseExit':
            idx_exit = i
            break

    if idx_exit is None:
        return None

    for i in range(idx_exit - 1, -1, -1):
        nome_evento = eventos[i].get('event')
        if nome_evento == 'SupercruiseDestinationDrop':
            return True
        if nome_evento in ('SupercruiseEntry', 'StartJump'):
            return False

    return None

# ==========================================
# 3. FASE 0: SALTO E TELEMETRIA
# ==========================================
def aguardar_mass_lock_livre(timeout=90):
    """ Só vale a pena tentar o salto sem Mass Lock -- caso contrário o FSD
    nem chega a carregar (o diagnóstico mais abaixo em iniciar_salto_seguro
    já apanhava isto, mas só depois de gastar um 'j' e 8s de aceleração
    para nada). Mass Lock costuma ser só questão de distância (ainda perto
    da estação/carrier/corpo celeste, ex: logo a seguir ao undocking) --
    por isso espera em vez de abortar logo; só desiste se não desbloquear
    dentro do timeout. """
    print("\n>>> A confirmar telemetria: sem Mass Lock antes do salto...")
    limite = time.time() + timeout
    while time.time() < limite:
        flags = ler_telemetria()
        if not bool(flags & STATUS_FLAGS["FSD_MASS_LOCKED"]):
            print("[OK] Sem Mass Lock -- livre para saltar.")
            return True
        time.sleep(1)

    abortar_com_erro(f"Timeout ({timeout}s) à espera que o Mass Lock desligue. "
                      f"A nave pode estar presa perto de um corpo celeste/estação.")

def iniciar_salto_seguro(tentativas_mass_lock=0):
    # Se já estamos em Supercruise (ex: reentrada nesta função depois de uma
    # falha mais à frente no fluxo, como engatar_assistencia_menu(), sem a
    # nave ter saído de Supercruise entretanto), não faz sentido premir 'j'
    # outra vez -- pode cancelar o salto em curso ou, pior, iniciar outro
    # salto para um destino diferente (o 'j' é a mesma tecla para saltos
    # dentro do sistema e hyperspace).
    if bool(ler_telemetria() & STATUS_FLAGS["SUPERCRUISE"]):
        print("[OK] Já em Supercruise -- a saltar o 'j', segue direto para a confirmação.")
        return True

    aguardar_mass_lock_livre()

    print("\n>>> FASE 0: Iniciar Salto (J)...")
    pydirectinput.press('j')

    # Watchdog: Confirmar CHARGING -- vigia também o Mass Lock em tempo
    # real, não só no diagnóstico pós-falha mais abaixo. Já aconteceu o FSD
    # começar a carregar e ser interrompido a meio por um Mass Lock
    # transitório ("Mass-Locking" no ecrã, a barra para de encher) que já
    # tinha voltado a desligar-se pelo tempo que o diagnóstico pós-falha
    # (uma única leitura de telemetria, ~23s depois do 'j') lá chega --
    # nunca apanhava, e caía sempre no fallback genérico "obstruído por
    # corpo celeste".
    contagem_limpo = 0
    mass_lock_interrompeu = False
    MAX_TENTATIVAS_MASS_LOCK = 3
    while contagem_limpo < 15:
        if bool(ler_telemetria() & STATUS_FLAGS["FSD_MASS_LOCKED"]):
            mass_lock_interrompeu = True
            break
        if procurar_template(templates['charging'], "CHARGING", MONITOR_CENTER, 0.85):
            contagem_limpo += 1
            print(f">>> FASE 1: Charging {contagem_limpo}s")
            time.sleep(1)
        else:
            break

    if mass_lock_interrompeu:
        pydirectinput.press('x')  # cancela a tentativa de salto a meio
        msg = (f"Mass Lock interrompeu o carregamento do FSD a meio do salto "
               f"(tentativa {tentativas_mass_lock+1}/{MAX_TENTATIVAS_MASS_LOCK}).")
        print(f"\n[AVISO] {msg}")
        _logger.error(msg)
        if tentativas_mass_lock >= MAX_TENTATIVAS_MASS_LOCK:
            abortar_com_erro(f"Mass Lock interrompeu o salto {MAX_TENTATIVAS_MASS_LOCK}x seguidas. "
                              f"Intervenção manual necessária.")
        return iniciar_salto_seguro(tentativas_mass_lock + 1)

    # FASE 2: acelerar a 100% para o salto. Substitui a antiga rotina de
    # impulsos repetidos na tecla '.' (com deteção do aviso THROTTLE UP,
    # cooldown de inércia, etc.) por um único press em 'right shift', que no
    # jogo salta diretamente para 100% de aceleração -- muito mais simples e
    # sem depender de deteção visual nenhuma. Confirmado com
    # debug/testar_rightshift.py.
    print(">>> FASE 2: A acelerar a 100% (right shift)...")
    pydirectinput.press('rightshift')

    # Deteção visual do THROTTLE UP a desaparecer não é fiável aqui: a
    # inércia da nave faz o símbolo sair do sítio/ampliar, o que se confunde
    # com "desapareceu". Sem telemetria de velocidade disponível no
    # Status.json, ficamos com uma espera fixa mais generosa (5s -> 8s) para
    # dar tempo à nave de realmente atingir 100% antes de cortar o throttle.
    time.sleep(8)


    print("[TELEMETRIA] A validar telemetria do FSD ...")
    flags = ler_telemetria()
    
    # 1. Verifica se está a carregar
    if bool(flags & STATUS_FLAGS["FSD_CHARGING"]):
        print("[OK] Motor FSD em carga confirmada pela telemetria.")
        # Só regista automaticamente se: (a) este salto não precisou do
        # retry interno de Mass Lock, e (b) a perna toda (desde o
        # undocking) correu sem nenhum erro/retry noutra etapa -- ver
        # infra_bridge.leg_esta_limpa(). Sem isto, uma observação
        # "automática" não garante nada sobre intervenção humana, e em
        # produção chegaram a ser 88% do dataset, afogando as poucas
        # observações manuais fiáveis no ajuste do los_checker.
        if tentativas_mass_lock == 0 and leg_esta_limpa():
            registar_los_visivel_auto()
        else:
            print("[LOS-AUTO] Perna com erro/retry -- observação não registada "
                  "(evita contaminar o ajuste com um salto que pode ter tido ajuda).")
        # Sem 'x' aqui -- ainda não estamos em Supercruise, só a carregar
        # o FSD. O 'x' só faz sentido logo a seguir a ENTRAR em Supercruise
        # (ver executar() / executar_plano_de_fuga(), depois de
        # aguardar_supercruise_confirmado()), para estabilizar a visão
        # antes de mexer nos menus -- não durante a carga.
        time.sleep(4.5)  # Espera o salto acontecer
        return True

    # Salto rápido: o carregamento do FSD e a entrada em Supercruise podem
    # ter acontecido dentro dos 8s de espera acima, e por essa altura a
    # FSD_CHARGING já está outra vez a False (mutuamente exclusiva com
    # SUPERCRUISE). Sem este check, cairia direto no diagnóstico de falha
    # abaixo -- que começa por um 'x' ainda antes de confirmar que não
    # entrámos em Supercruise, violando a regra de só cortar velocidade
    # depois de confirmar a entrada -- e podia abortar com um erro errado
    # ("obstruído por corpo celeste") apesar do salto ter sido bem-sucedido.
    if bool(flags & STATUS_FLAGS["SUPERCRUISE"]):
        print("[OK] Já em Supercruise (salto rápido) -- sem necessidade de diagnóstico.")
        return True

    pydirectinput.press('x')

    # 2. Se não está a carregar, usa as regras do Hermes para descobrir o porquê
    if bool(flags & STATUS_FLAGS["FSD_MASS_LOCKED"]):
        pydirectinput.press('x') 
        abortar_com_erro("Nave bloqueada por Mass Lock da estação/planeta.")
    
    if bool(flags & STATUS_FLAGS["HARDPOINTS_DEPLOYED"]):
        pydirectinput.press('x') 
        abortar_com_erro("Armas ou Trem de Aterragem abertos. Impossível saltar.")
        
    if procurar_template(templates['align_warning'], "ALIGN", MONITOR_CENTER, 0.82):
        pydirectinput.press('x') 
        abortar_com_erro("Vetor de proa totalmente desalinhado do destino.")
        
    pydirectinput.press('x')
    abortar_com_erro("Destino fisicamente obstruído por corpo celeste (Planeta/Estrela) nas costas.")

def registar_los_visivel_auto():
    """ Um salto de supercruise iniciado com sucesso implica que o alvo
    estava VISÍVEL (sem planeta no meio) e a nave ainda está junto à
    estação/carrier de partida -- é uma observação LOS 'visivel' de boa
    qualidade para alimentar o auto-fit (fase+período) do checker. Reusa
    registar_observacao()/obter_sistema_atual() do los_checker.py (mesma
    ligação à BD, mesma deteção de sistema pelo journal, já testadas) em
    vez de duplicar essa lógica aqui. Nunca pode partir o voo: qualquer
    falha aqui é só reportada e ignorada. """
    try:
        from los_checker import obter_sistema_atual, registar_observacao

        sistema = obter_sistema_atual(LOG_DIR)
        if not sistema:
            print("[LOS-AUTO] Sistema desconhecido -- observacao nao registada.")
            return

        registar_observacao(
            sistema, "visivel",
            "automatica: salto supercruise iniciado com sucesso em modo auto "
            "(pelo sim pelo nao: pode ter tido ajuda do utilizador)",
            origem="auto-linux"
        )
    except Exception as e:
        print(f"[LOS-AUTO] Falha ao registar observacao (ignorada, o voo continua): {e}")

def aguardar_supercruise_confirmado(timeout=30):
    """Confirma pela telemetria (Status.json, flag SUPERCRUISE=0x10) que já
    estamos mesmo em supercruise antes de tentar ativar o assist no menu --
    sem isto, o menu pode ser aberto ainda em espaco normal/transicao."""
    print("\n>>> A confirmar entrada em Supercruise pela telemetria...")
    limite = time.time() + timeout
    while time.time() < limite:
        flags = ler_telemetria()
        if bool(flags & STATUS_FLAGS["SUPERCRUISE"]):
            print("[OK] Supercruise confirmado pela telemetria.")
            return True
        time.sleep(0.5)

    abortar_com_erro(f"Timeout ({timeout}s). Supercruise nunca foi confirmado pela telemetria.")

# ==========================================
# 4. FASE 1: NAVEGAÇÃO MECÂNICA NO MENU
# ==========================================
def engatar_assistencia_menu():
    print("\n>>> FASE 1: Navegação no Painel Esquerdo...")

    # Garantir sempre o 'x' primeiro -- se estivermos em supercruise speed,
    # a nave oscila e nenhuma leitura de bússola/HUD (nem os templates
    # ACTIVE/INACTIVE abaixo) é de confiar sem isto. Corre incondicionalmente,
    # antes de qualquer verificação, para não depender de nenhum caminho do
    # código lá mais a baixo o garantir.
    pydirectinput.press('x')

    # 'd'+'space' lá dentro do painel é um TOGGLE, não um "ligar" -- se esta
    # função for chamada outra vez (retry depois de uma falha mais à frente
    # no fluxo, ex: _alinhar_com_olho() a abortar por timeout mesmo com o
    # Assist já ligado e a funcionar), abrir o painel e voltar a premir
    # desligava o que já estava a funcionar. Verifica ANTES de abrir
    # qualquer painel (para não arriscar o texto ficar tapado pelo próprio
    # painel).
    #
    # "SUPERCRUISE_ASSIST_INACTIVE.png" (chave 'align_warning') NÃO significa
    # "toggle desligado" -- é o aviso mostrado quando o assist já está
    # ligado mas ainda não conseguiu alinhar (à espera do alvo). Ou seja:
    # tanto o ACTIVE como o INACTIVE no ecrã confirmam que o toggle já está
    # ON (só muda o sub-estado -- a controlar vs. à espera de alinhamento).
    # Só se assume mesmo OFF quando NENHum dos dois aparece -- só nesse caso
    # se avança para o toggle.
    ja_ativo = assist_esta_ativo()
    ativo_a_espera_alinhamento = procurar_template(templates['align_warning'], "ASSIST INACTIVE (a espera de alinhamento)", MONITOR_CENTER, 0.82)
    if ja_ativo or ativo_a_espera_alinhamento:
        print(">>> Assistência já parece ligada (ativa, ou ativa mas ainda não alinhada) -- nada a fazer.")
        return True

    # Cada portão (NAVIGATION, depois confirmação do alvo) é validado antes
    # de avançar para o próximo input -- nunca se carrega às cegas. Se
    # qualquer um falhar, repõe com 2x backspace e tenta a sequência
    # inteira outra vez, até 3 tentativas.
    MAX_TENTATIVAS = 3
    for tentativa in range(1, MAX_TENTATIVAS + 1):
        print(f"\n>>> [TENTATIVA {tentativa}/{MAX_TENTATIVAS}] Abrindo painel lateral (1)...")
        pydirectinput.press('1')
        time.sleep(1)

        # 1. Espera a aba NAVIGATION aparecer -- cicla com 'q' enquanto não
        # aparecer. 10s na primeira tentativa; 5s nas seguintes (já é pelo
        # menos a segunda vez a abrir o painel -- se ainda assim não
        # aparecer, não é "precisa de mais tempo", é sinal de que algo está
        # mesmo errado, e não compensa continuar às cegas até esgotar
        # MAX_TENTATIVAS -- aborta logo aqui).
        timeout_nav = 10.0 if tentativa == 1 else 5.0
        nav_found = False
        tempo_fim_nav = time.time() + timeout_nav
        while time.time() < tempo_fim_nav:
            if procurar_template(templates['nav_tab'], "NAV TAB", MONITOR_PANEL, 0.61):
                nav_found = True
                break
            pydirectinput.press('q')
            time.sleep(0.5)

        if not nav_found and tentativa >= 2:
            abortar_com_erro(f"Aba NAVIGATION não apareceu mesmo depois de reabrir o painel "
                              f"(tentativa {tentativa}, timeout {timeout_nav:.0f}s) -- algo está muito errado.")

        if nav_found:
            print(">>> Aba NAVIGATION confirmada -- a focar no destino pré-selecionado (Space)...")
            pydirectinput.press('space')
            time.sleep(0.8)

            # 2. Valida que o painel mostra mesmo o alvo certo (Zahir ou a
            # estação) antes de tentar ativar a assistência -- sem isto, um
            # 'e'+'space' às cegas podia ativar o botão errado.
            alvo_confirmado = (
                procurar_template(templates['confirma_carrier'], "CONFIRMA CARRIER", MONITOR_PANEL, 0.80) or
                procurar_template(templates['confirma_station'], "CONFIRMA STATION", MONITOR_PANEL, 0.80)
            )

            if alvo_confirmado:
                print(">>> Alvo confirmado -- a ativar Assistência (D + Space)...")
                pydirectinput.press('d')
                time.sleep(0.5)
                pydirectinput.press('space')
                time.sleep(1.0)
                pydirectinput.press('backspace')  # fecha painel
                print(">>> Painel fechado. Assistência ligada.")
                # Não se confirma "ASSIST ACTIVE" aqui -- essa mensagem só
                # aparece depois do alvo estar alinhado (o toggle liga-se
                # já, mas fica "à espera" até _alinhar_com_olho() apontar a
                # nave). Confirmar isto aqui abortava sempre, mesmo com o
                # toggle a funcionar bem. A confirmação real acontece no
                # primeiro watchdog do monitorar_viagem(), já depois do
                # alinhamento.
                return True

            print(f"[AVISO] Painel não confirma o alvo certo (tentativa {tentativa}/{MAX_TENTATIVAS}).")
        else:
            print(f"[AVISO] Aba NAVIGATION não confirmada (tentativa {tentativa}/{MAX_TENTATIVAS}).")

        # Falhou nesta tentativa -- repõe com 2x backspace e tenta outra vez.
        pydirectinput.press('backspace')
        time.sleep(0.3)
        pydirectinput.press('backspace')
        time.sleep(0.5)

    abortar_com_erro(f"Falha ao ativar Supercruise Assist após {MAX_TENTATIVAS} tentativas "
                      f"(aba NAVIGATION ou confirmação do alvo nunca bateram).")

# ==========================================
# 5. FASE 2: VIAGEM E CHEGADA
# ==========================================
PASSO_EVASIVO_COOLDOWN = 1.5  # segundos de pausa depois do boost -- com velocidade a nave oscila muito, não dá para confiar em nenhuma leitura de bússola/HUD durante a fuga, por isso nem se tenta alinhar nesta fase

def _passo_evasivo():
    """ 'rightshift' (100% de aceleração, mesma tecla confirmada em
    iniciar_salto_seguro) + 'tab' (boost) + 'v' (heatsink -- faz os
    piratas perderem a capacidade de dar target à nave durante um tempo),
    incondicional e cíclico. Não tenta alinhar nada aqui -- com velocidade
    a nave oscila demais para qualquer leitura de bússola/HUD ser de
    confiar; entrar em Supercruise não exige apontar a nada (só sair dela
    para um alvo específico é que precisa), por isso a fuga foca-se só em
    ganhar distância e perder o lock de quem estiver a perseguir. """
    pydirectinput.press('rightshift')
    pydirectinput.press('tab')
    pydirectinput.press('v')
    time.sleep(PASSO_EVASIVO_COOLDOWN)

def pode_saltar_agora():
    """ Confirma que não há hardpoints em baixo antes de tentar o salto --
    recolhe com 'u' se estiverem (armas fora impedem o FSD de carregar,
    ver diagnóstico em iniciar_salto_seguro). Devolve True se ficou livre. """
    if bool(ler_telemetria() & STATUS_FLAGS["HARDPOINTS_DEPLOYED"]):
        print("[PLANO DE FUGA] Hardpoints em baixo -- a recolher ('u')...")
        pydirectinput.press('u')
        time.sleep(1.0)
    return not bool(ler_telemetria() & STATUS_FLAGS["HARDPOINTS_DEPLOYED"])

IMPULSO_EFUSIVO = 0.35  # bem maior que o IMPULSO_HUD (0.17s) ou o impulso
# base da bússola (1x IMPULSO_BUSSOLA=0.22s) do olho.py -- com o Supercruise
# Assist já ligado, a nave é "sugada" para o centro assim que passa perto do
# alvo, por isso não compensa a distinção fina entre correção macro
# (bússola, longe) e micro (HUD, perto): um único impulso mais forte chega
# lá mais depressa nos dois casos, sem precisão cirúrgica nenhuma.

COOLDOWN_PULSO_EFUSIVO = 2.0  # mesmo valor testado do olho.py (aplicar_manobra_hud
# e aplicar_manobra_bussola usam os dois 2.0s) -- a nave continua a rodar por
# inércia depois de largar as teclas, mais ainda com o impulso mais forte
# (0.35s vs. 0.17s/0.22s); ler bússola/HUD antes disto assentar dá leituras
# a meio do movimento e alimenta correções em cima de correções.

COOLDOWN_MAX = 4.0  # teto absoluto -- mesmo o impulso mais forte (OCA, 8x)
# já deixa o nariz assente bem antes disto; o COOLDOWN_PULSO_EFUSIVO*8 (16s)
# só atrasava o alinhamento sem ganhar precisão nenhuma. Aplicado dentro de
# _pulso_efusivo() para valer em qualquer chamada, atual ou futura.

FATOR_AD = 2  # 'a'/'d' (yaw) ficam pressionadas o dobro do tempo de 'w'/'s'
# (pitch) -- mesmo fator já aplicado em olho.py (aplicar_manobra_bussola/
# aplicar_manobra_hud) porque a nave gira mais devagar no eixo de yaw, mas
# este _pulso_efusivo é uma função separada e nunca tinha recebido o
# mesmo fix -- confirmado em produção (2026-08-21): bola completamente
# parada (mesmo dx/dy) ao longo de várias correções "W + D" seguidas.

def _pulso_efusivo(teclas, impulso=None, cooldown=None):
    if impulso is None: impulso = IMPULSO_EFUSIVO
    if cooldown is None: cooldown = COOLDOWN_PULSO_EFUSIVO
    cooldown = min(cooldown, COOLDOWN_MAX)
    for t in ["w", "s", "a", "d"]:
        if t not in teclas: pydirectinput.keyUp(t)
    if teclas:
        for t in teclas: pydirectinput.keyDown(t)
        time.sleep(impulso)
        teclas_verticais = [t for t in teclas if t in ("w", "s")]
        teclas_horizontais = [t for t in teclas if t in ("a", "d")]
        for t in teclas_verticais: pydirectinput.keyUp(t)
        if teclas_horizontais:
            time.sleep(impulso * (FATOR_AD - 1))
            for t in teclas_horizontais: pydirectinput.keyUp(t)
        time.sleep(cooldown)

TAM_JANELA_ALINHAR = 700  # janela de debug quadrada -- a área capturada
# (MONITOR_CONFIG por nave) é sempre quase quadrada (ex: 58x68 no
# Mediumtransport01); o tamanho antigo (1200x490, muito mais largo que
# alto) esticava horizontalmente e distorcia os círculos do HUD em
# elipses, dificultando avaliar visualmente a posição real da bola/nariz.

def _alinhar_com_olho(nome_janela, limite_segundos=60.0):
    """ Loop de alinhamento reaproveitando a bússola do olho.py (já testada e
    calibrada por nave). Chamado sempre DEPOIS do Supercruise Assist estar
    ligado -- com o assist ligado o alvo centra muito mais depressa ("como
    um íman") do que tentar à mão, por isso o teto de tempo aqui é bem mais
    curto do que uma manobra manual precisaria.

    SEM o retículo do alvo no HUD (olho.localizar_alvo_hud) -- essa parte
    não dava para validar: perto de bater o match, a nave acelerava (zona
    PERTO/LONGE/BORDA calculada a partir de dx_hud/dy_hud) e passava para o
    outro lado antes do match sequer confirmar, ficando presa nesse ciclo
    indefinidamente. Só a bússola decide agora.

    Não chama olho.executar() diretamente -- essa função dá sys.exit(0) ao
    alinhar, o que o vasco.py interpretaria como esta etapa
    (supercruise_assist.py) ter terminado a meio. Reaproveita só as
    funções de visão/correção já testadas. """
    # Folga para a animação de fecho do painel (backspace, em
    # engatar_assistencia_menu) terminar antes da primeira leitura de
    # ecrã -- sem isto, a bússola/HUD podiam ler o ecrã ainda a meio da
    # transição do jogo e ficar mal posicionados logo à partida.
    time.sleep(1.5)

    import olho

    nave_ativa = olho.obter_modelo_nave_atual()
    MONITOR_CONFIG, CX_NEUTRO, CY_NEUTRO = olho.carregar_dados_calibracao(nave_ativa)
    print(f"[INFO] Nave Actual: {nave_ativa}")
    print(f"[INFO] Dados Calibracao: MONITOR_CONFIG: {MONITOR_CONFIG}\n    CX_NEUTRO: {CX_NEUTRO} CY_NEUTRO: {CY_NEUTRO}")

    def _gravar_snapshot_alinhado(sct, area_bussola, img_bussola=None, coords_bola=None):
        """ Grava exatamente o frame mostrado na janela de debug no instante
        em que o alinhamento é confirmado -- chamado sempre ANTES de
        destroyWindow(). Diferente do LOG_BUSSOLA (sobrescrito a cada
        iteração seguinte, já não corresponde ao momento da decisão quando
        se olha para ele depois) -- este fica congelado nesse instante para
        avaliar CX_NEUTRO/CY_NEUTRO contra o que a bola mostrava mesmo ali. """
        try:
            if img_bussola is None:
                img_bussola = cv2.cvtColor(np.array(sct.grab(area_bussola)), cv2.COLOR_BGRA2BGR)
                coords_bola = None
            img_debug = img_bussola.copy()
            cv2.rectangle(img_debug, (CX_NEUTRO-olho.DEAD_ZONE_BUSSOLA, CY_NEUTRO-olho.DEAD_ZONE_BUSSOLA),
                                      (CX_NEUTRO+olho.DEAD_ZONE_BUSSOLA, CY_NEUTRO+olho.DEAD_ZONE_BUSSOLA), (255, 255, 255), 1)
            cv2.circle(img_debug, (CX_NEUTRO, CY_NEUTRO), 1, (0, 165, 255), -1)
            if coords_bola:
                cv2.circle(img_debug, coords_bola, 3, (0, 255, 0), -1)
            view_zoom = cv2.resize(img_debug, (TAM_JANELA_ALINHAR, TAM_JANELA_ALINHAR), interpolation=cv2.INTER_NEAREST)
            cv2.imwrite(LOG_ALINHADO, view_zoom)
            print(f"[ALINHAR] Snapshot do momento do alinhamento gravado em {LOG_ALINHADO}")
        except Exception as e:
            print(f"[AVISO] Falha ao gravar snapshot de alinhamento ({e}).")

    # Janela de debug sempre visível aqui (independente de VISUAL_DEBUG) --
    # mesmo estilo do olho.py, para dar visibilidade real ao que a bússola/
    # HUD estão a ver durante o alinhamento. É só visibilidade, não pode
    # derrubar o alinhamento -- já aconteceu falhar a criar a janela (Qt
    # sem o plugin "wayland" nesta sessão) e nunca mais correr nada.
    debug_disponivel = True
    try:
        cv2.namedWindow(nome_janela, cv2.WINDOW_NORMAL)
        # Quadrada (ver TAM_JANELA_ALINHAR) para não distorcer o HUD.
        cv2.resizeWindow(nome_janela, TAM_JANELA_ALINHAR, TAM_JANELA_ALINHAR)
        # Fora da área do jogo -- mesmo critério do olho.py: ecrã
        # secundário se existir, senão a metade direita do ultrawide.
        with mss.mss() as sct_pos:
            monitores_pos = sct_pos.monitors
        if len(monitores_pos) > 2:
            ecra_secundario = monitores_pos[2]
            cv2.moveWindow(nome_janela, ecra_secundario["left"] + 20, ecra_secundario["top"] + 50)
        else:
            cv2.moveWindow(nome_janela, 1950, 50)

        # Criar/mover a janela costuma roubar o foco da janela do jogo (o
        # KWin ativa a janela recém-mapeada) -- sem repor o foco aqui, as
        # teclas seguintes (pydirectinput) não chegam ao Elite Dangerous.
        # Mesmo padrão do olho.py: dar tempo ao KWin para assentar a janela
        # e só depois forçar o foco de volta.
        for _ in range(5):
            cv2.waitKey(50)
        time.sleep(0.5)
        focar_jogo_seguro()
        time.sleep(0.5)
    except Exception as e:
        print(f"[AVISO] Não foi possível criar/posicionar a janela de debug ({e}) -- a continuar sem visualização.")
        debug_disponivel = False

    with mss.mss() as sct:
        try: monitor_jogo = sct.monitors[1]
        except: monitor_jogo = sct.monitors[0]
        area_bussola = {
            "top": monitor_jogo["top"] + MONITOR_CONFIG["top"],
            "left": monitor_jogo["left"] + MONITOR_CONFIG["left"],
            "width": MONITOR_CONFIG["width"], "height": MONITOR_CONFIG["height"]
        }

        # Estabilização final antes da primeira leitura. Fica de fora do
        # relógio de limite_segundos abaixo -- não é tempo de correção, é só
        # espera. Não é um time.sleep() simples -- sem chamar
        # cv2.imshow()/waitKey() durante a espera, o Qt nunca chega a
        # desenhar nada na janela (não é "a demorar a atualizar", é estar
        # mesmo sem nenhum frame mostrado, por isso fica preta até ao
        # primeiro ciclo do loop principal lá em baixo). Mostra o que já
        # está a ver desde já e mantém o waitKey a correr durante a espera.
        #
        # Aproveita estes mesmos frames para aquecer o histórico da bússola
        # (localizar_bola só devolve uma direção real a partir da 3ª
        # amostra -- ver olho.py) e disparar um único impulso assim que
        # houver uma leitura de confiança, em vez de ficar parada os 7s
        # completos sem reagir -- a nave já começa a corrigir enquanto a
        # janela de debug ainda está a assentar.
        tempo_fim_settle = time.time() + 7.0
        toque_inicial_dado = False
        while time.time() < tempo_fim_settle:
            try:
                img_settle = cv2.cvtColor(np.array(sct.grab(area_bussola)), cv2.COLOR_BGRA2BGR)
            except Exception:
                time.sleep(0.05)
                continue

            if debug_disponivel:
                try:
                    cv2.imshow(nome_janela, cv2.resize(img_settle, (TAM_JANELA_ALINHAR, TAM_JANELA_ALINHAR)))
                    cv2.waitKey(50)
                except Exception:
                    time.sleep(0.05)

            if not toque_inicial_dado:
                cmd_settle, _, _, _, _ = olho.localizar_bola(img_settle, CX_NEUTRO, CY_NEUTRO)
                if cmd_settle not in ("AQUECENDO", "NÃO_DETETADO"):
                    if cmd_settle != "ALINHADO_MACRO":
                        comando_puro_settle = cmd_settle[len("OCA:"):] if cmd_settle.startswith("OCA:") else cmd_settle
                        teclas_settle = [t for t, letra in (('w', 'W'), ('s', 'S'), ('a', 'A'), ('d', 'D')) if letra in comando_puro_settle]
                        if teclas_settle:
                            print(f"[ALINHAR] Toque inicial de correção: {cmd_settle}")
                            _pulso_efusivo(teclas_settle)
                    toque_inicial_dado = True
                    break

        tempo_inicio_centrado = None
        TEMPO_ESTABILIDADE_FINAL = 3.0
        # Tolerância do retículo do alvo no HUD (olho.localizar_alvo_hud,
        # MONITOR_HUD 359x302 centrado no ecrã) -- estimativa inicial, por
        # afinar com dados reais de LOG_ALINHADO/print de TRACE. Existe
        # porque a bússola (globo do dashboard) sozinha já mostrou dar
        # falsos positivos: declarou "alinhado" com o retículo do HUD
        # claramente fora do centro (confirmado 2026-08-20 ~04:45, ver
        # temp/seta.png). Não usa o retículo para CORRIGIR o rumo (o
        # mapeamento dx/dy -> wasd desta instrumento nunca foi validado
        # empiricamente, ao contrário do da bússola -- ver comentário em
        # olho.localizar_bola -- arriscar isso às cegas podia piorar a
        # correção em vez de ajudar); serve só de confirmação cruzada antes
        # de sair.
        HUD_TOLERANCIA_X = 40
        HUD_TOLERANCIA_Y = 35
        tempo_inicio_alinhar = time.time()

        # Caminho alternativo de saída: telemetria (SUPERCRUISE ainda
        # confirmada) + banner do Assist visível no ecrã, sustentados
        # CONTINUAMENTE durante 3s -- mesma janela de estabilidade que
        # TEMPO_ESTABILIDADE_FINAL, por consistência. Mais direto que o
        # caminho via bússola (não depende da calibração de CX_NEUTRO/
        # CY_NEUTRO, que tem andado a ser afinada à mão). Sai por
        # qualquer um dos dois caminhos que confirmar primeiro -- este ou
        # o da bússola mais abaixo. 3s contínuos (não 1 frame nem 1s)
        # porque já vimos o banner aparecer e reverter para
        # SUPERCRUISE_ASSIST_INACTIVE pouco depois (2026-08-20 ~04:40) --
        # uma janela mais longa filtra esse tipo de flicker de transição.
        tempo_inicio_confirmado_telemetria = None

        while True:
            if time.time() - tempo_inicio_alinhar > limite_segundos:
                abortar_com_erro(f"Alinhamento pós-Assist excedeu {limite_segundos}s. Intervenção manual necessária.")

            supercruise_ok = bool(ler_telemetria() & STATUS_FLAGS["SUPERCRUISE"])
            banner_ok = (assist_esta_ativo() or
                         procurar_template(templates['assist_active_big'], "ASSIST ACTIVE (grande)", MONITOR_CENTER, 0.70))
            if supercruise_ok and banner_ok:
                if tempo_inicio_confirmado_telemetria is None:
                    tempo_inicio_confirmado_telemetria = time.time()
                if time.time() - tempo_inicio_confirmado_telemetria >= TEMPO_ESTABILIDADE_FINAL:
                    print("[ALINHAR] Confirmado por telemetria + banner do Assist -- a retomar a viagem.")
                    falar("Realigned. Resuming journey.")
                    olho.largar_todas_as_teclas()
                    if debug_disponivel:
                        _gravar_snapshot_alinhado(sct, area_bussola)
                        cv2.destroyWindow(nome_janela)
                    return
            else:
                tempo_inicio_confirmado_telemetria = None

            img_bussola = cv2.cvtColor(np.array(sct.grab(area_bussola)), cv2.COLOR_BGRA2BGR)
            cmd_bussola, coords_bola, _, dist_x, dist_y = olho.localizar_bola(img_bussola, CX_NEUTRO, CY_NEUTRO)
            comando_display = ""

            if cmd_bussola == "ALINHADO_MACRO":
                olho.largar_todas_as_teclas()

                # Confirmação cruzada -- a bússola sozinha já não chega
                # (ver HUD_TOLERANCIA_X acima). Só conta como "aligned" se
                # o retículo do alvo no HUD TAMBÉM estiver perto do centro.
                # Se discordarem, não sai -- reseta a contagem e volta ao
                # loop (o Assist continua a puxar a nave, a bússola pode
                # voltar a corrigir na iteração seguinte).
                hud_ok, dx_hud, dy_hud, _, hud_val = olho.localizar_alvo_hud(sct)
                hud_confirma = hud_ok and abs(dx_hud) <= HUD_TOLERANCIA_X and abs(dy_hud) <= HUD_TOLERANCIA_Y

                # O aviso nativo do próprio jogo -- "ALIGN WITH TARGET
                # DESTINATION" (template align_warning, já carregado e já
                # usado noutro sítio deste ficheiro com threshold 0.82) --
                # é o sinal mais autoritativo possível: é o jogo a dizer
                # diretamente que ainda não está alinhado. Prevalece sobre
                # bússola e HUD -- confirmado em produção (2026-08-20
                # ~05:02: banner bem visível no ecrã, ver
                # temp/Screenshot_20260820_050246.png, com a bússola já a
                # achar que estava tudo bem).
                ainda_avisa = procurar_template(templates['align_warning'], "ALIGN WARNING", MONITOR_CENTER, 0.82)

                if ainda_avisa or not hud_confirma:
                    tempo_inicio_centrado = None
                    if ainda_avisa:
                        comando_display = "BUSSOLA OK MAS JOGO AINDA AVISA 'ALIGN WITH TARGET DESTINATION'"
                    elif hud_ok:
                        comando_display = f"BUSSOLA OK MAS HUD DISCORDA (dx={dx_hud} dy={dy_hud} match={hud_val:.2f})"
                    else:
                        comando_display = f"BUSSOLA OK MAS HUD NAO DETETOU RETICULO (match={hud_val:.2f})"
                    print(f"[ALINHAR] {comando_display}")
                else:
                    comando_display = "ALINHADO (bussola + HUD + sem aviso do jogo)"
                    if tempo_inicio_centrado is None:
                        tempo_inicio_centrado = time.time()
                    if time.time() - tempo_inicio_centrado >= TEMPO_ESTABILIDADE_FINAL:
                        print("[ALINHAR] Alvo realinhado -- a retomar a viagem.")
                        falar("Realigned. Resuming journey.")
                        if debug_disponivel:
                            _gravar_snapshot_alinhado(sct, area_bussola, img_bussola, coords_bola)
                            cv2.destroyWindow(nome_janela)
                        return
            elif not coords_bola:
                tempo_inicio_centrado = None
                comando_display = "NAO_DETETADO"
                olho.largar_todas_as_teclas()
                olho.aplicar_roll_desocluir(
                    "alinhamento pos-assist -- bussola NAO_DETETADO -- possivel sol/planeta a tapar")
            else:
                tempo_inicio_centrado = None
                is_oca = cmd_bussola.startswith("OCA:")
                comando_puro = cmd_bussola[len("OCA:"):] if is_oca else cmd_bussola
                teclas = []
                if "W" in comando_puro: teclas.append('w')
                if "S" in comando_puro: teclas.append('s')
                if "A" in comando_puro: teclas.append('a')
                if "D" in comando_puro: teclas.append('d')

                # O Assist, uma vez a puxar visivelmente (banner
                # active/dbl/big já no ecrã), já está a corrigir sozinho --
                # um impulso manual em cima disso, mesmo pequeno, pode
                # ultrapassar a janela mesmo apertada do alinhamento real
                # (DEAD_ZONE_BUSSOLA=2px) e empurrar a nave para fora outra
                # vez, reacendendo o "ALIGN WITH TARGET DESTINATION" --
                # ciclo confirmado em produção (2026-08-20: banner ativo ->
                # impulso manual do script -> saiu da zona -> aviso voltou
                # -> repete). Se o banner já estiver visível, não mexe em
                # nada -- deixa o Assist terminar sozinho; a bússola só
                # volta a corrigir se o banner desaparecer de facto.
                if (assist_esta_ativo() or
                        procurar_template(templates['assist_active_big'], "ASSIST ACTIVE (grande)", MONITOR_CENTER, 0.70)):
                    comando_display = f"ASSIST JA A PUXAR -- sem impulso manual ({cmd_bussola})"
                elif is_oca:
                    # Bola oca = alvo por detrás da nave -- não é "longe",
                    # é para além do 3/3 (mais extremo que a berma do 1/3).
                    # Precisa de muita energia para lá chegar -- 8x o
                    # impulso base (cooldown a acompanhar), não o mesmo
                    # tratamento de uma correção normal.
                    comando_display = f"AJUSTE BUSSOLA (ALVO ATRAS): {cmd_bussola}"
                    _pulso_efusivo(teclas, IMPULSO_EFUSIVO * 8, COOLDOWN_PULSO_EFUSIVO * 8)
                else:
                    # Perto do limite da dead zone (DEAD_ZONE_BUSSOLA=2px --
                    # muito apertado), a leitura pode ainda estar a assentar
                    # de uma correção anterior, não ser genuinamente longe.
                    # Um impulso cheio ("exagero") aí ultrapassa o centro, e
                    # a próxima leitura corrige para o lado oposto -- ciclo
                    # sem fim. Reduz o impulso perto do limite; cooldown
                    # fica cheio (é assentar, não força) para dar tempo à
                    # próxima leitura de ser de confiar.
                    ref_x_bussola = MONITOR_CONFIG["width"] / 2
                    ref_y_bussola = MONITOR_CONFIG["height"] / 2
                    fracao_bussola = max(dist_x / ref_x_bussola, dist_y / ref_y_bussola)

                    if fracao_bussola <= (1 / 3):
                        comando_display = f"AJUSTE BUSSOLA (perto do limite): {cmd_bussola}"
                        _pulso_efusivo(teclas, IMPULSO_EFUSIVO * (1 / 2), COOLDOWN_PULSO_EFUSIVO)
                    else:
                        comando_display = f"AJUSTE BUSSOLA: {cmd_bussola}"
                        _pulso_efusivo(teclas)

            # Debug visual -- só a bússola agora (sem o painel do HUD, ver
            # docstring). O LOG_BUSSOLA grava-se sempre em disco, mesmo sem
            # janela GUI (mesmo padrão forense do LOG_TEST em
            # procurar_template()) -- exatamente no referencial de pixels
            # que o bot usa (mss.grab), com CX_NEUTRO/CY_NEUTRO desenhados
            # por cima, para validar/corrigir a calibração sem depender de
            # screenshots manuais tirados noutra escala ou com decorações
            # da janela (que não mapeiam 1:1 para estas coordenadas).
            img_debug = img_bussola.copy()
            try:
                cv2.rectangle(img_debug, (CX_NEUTRO-olho.DEAD_ZONE_BUSSOLA, CY_NEUTRO-olho.DEAD_ZONE_BUSSOLA),
                                          (CX_NEUTRO+olho.DEAD_ZONE_BUSSOLA, CY_NEUTRO+olho.DEAD_ZONE_BUSSOLA), (255, 255, 255), 1)
                cv2.circle(img_debug, (CX_NEUTRO, CY_NEUTRO), 1, (0, 165, 255), -1)
                if coords_bola:
                    cv2.circle(img_debug, coords_bola, 3, (0, 255, 0), -1)
                cv2.imwrite(LOG_BUSSOLA, img_debug)
            except Exception as e:
                print(f"[AVISO] Falha a gravar {LOG_BUSSOLA} ({e}).")

            if debug_disponivel:
                try:
                    view_zoom = cv2.resize(img_debug, (TAM_JANELA_ALINHAR, TAM_JANELA_ALINHAR), interpolation=cv2.INTER_NEAREST)
                    cv2.putText(view_zoom, comando_display, (14, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 255, 0), 2)

                    cv2.imshow(nome_janela, view_zoom)
                    cv2.waitKey(1)
                except Exception as e:
                    print(f"[AVISO] Falha a atualizar a janela de debug ({e}) -- a desligar a visualização.")
                    debug_disponivel = False

            time.sleep(0.04)

def executar_plano_de_fuga():
    """ Saída inesperada de Supercruise (interdição ou qualquer outro
    motivo -- não interessa qual, ver monitorar_viagem). Três fases:

    1) Evasão cíclica (_passo_evasivo, incondicional) + pode_saltar_agora()
       + tentativa de 'j', repetido até a telemetria confirmar
       FSD_CHARGING. Não tenta alinhar nada nesta fase -- ver
       _passo_evasivo para o porquê.
    2) FSD confirmado a carregar -- acelera a 100% e espera a confirmação
       de Supercruise pela telemetria (mesmo caminho do
       iniciar_salto_seguro, sem repetir o 'j' -- já sabemos que carregou).
    3) Já em Supercruise: 'x' para parar e estabilizar a visão, liga o
       Supercruise Assist (engatar_assistencia_menu) e só DEPOIS alinha
       com as funções do olho.py -- com o assist já ligado o alvo centra
       muito mais depressa ("como um íman") do que tentar à mão a
       oscilar. Quando estável, devolve o controlo -- já com o assist
       ligado, por isso monitorar_viagem() não precisa de o voltar a
       ligar.

    Não chama olho.executar() nem iniciar_salto_seguro() diretamente --
    o primeiro dá sys.exit(0) ao alinhar (o vasco.py pensaria que esta
    etapa tinha terminado a meio da fuga); o segundo voltaria a premir 'j'
    e a repetir o pré-check de Mass Lock sem necessidade, já sabemos que o
    FSD carregou nesta função. Reaproveita só as funções de visão/correção
    já testadas (via _alinhar_com_olho). """
    print("\n>>> PLANO DE FUGA: A evadir e a tentar reentrar em Supercruise...")
    falar("Interdiction detected. Executing evasion plan.")

    # Fase 1: evasão cíclica + tentativa de salto, até o FSD confirmar
    # carga pela telemetria.
    tempo_inicio_fuga = time.time()
    LIMITE_FUGA = 180.0

    while True:
        if time.time() - tempo_inicio_fuga > LIMITE_FUGA:
            abortar_com_erro(f"Plano de fuga excedeu {LIMITE_FUGA}s sem conseguir reentrar em Supercruise. "
                              f"Intervenção manual necessária.")

        _passo_evasivo()
        pode_saltar_agora()

        pydirectinput.press('j')
        time.sleep(1.5)

        if bool(ler_telemetria() & STATUS_FLAGS["FSD_CHARGING"]):
            print("[PLANO DE FUGA] FSD a carregar -- a acelerar a 100% e a confirmar Supercruise...")
            break

    # Fase 2: FSD já a carregar -- acelera a 100% (mesma tecla/racional do
    # iniciar_salto_seguro) e espera a confirmação de Supercruise.
    pydirectinput.press('rightshift')
    aguardar_supercruise_confirmado()

    # Fase 3: em Supercruise -- estabiliza, liga o Assist, só depois alinha.
    print("[PLANO DE FUGA] Em Supercruise -- a estabilizar (x) e a ligar o Assist...")
    pydirectinput.press('x')
    time.sleep(1.0)
    engatar_assistencia_menu()

    print("[PLANO DE FUGA] Assist ligado -- a alinhar com o alvo...")
    _alinhar_com_olho("PLANO DE FUGA - Ocular", limite_segundos=150.0)

def monitorar_viagem(tentativas_fuga=0):
    print("\n>>> FASE 2: Viagem em Supercruise...")

    # Watchdog: Confirmar HUD
    contagem_limpo = 0
    timeout_assist = time.time() + 60
    while contagem_limpo < 3:
        if time.time() > timeout_assist:
            abortar_com_erro("Timeout (60s). Supercruise Assist não apareceu no HUD.")
        if not assist_esta_ativo():
            contagem_limpo = 0
        else:
            contagem_limpo += 1
        time.sleep(1)

    # Só agora e que o assist esta confirmado visualmente -- so agora anuncia.
    falar("Supercruise assist engaged. Monitoring flight path.")

    # Viagem Longa. A chegada é confirmada pelo Journal
    # (confirmar_chegada_por_journal) -- substitui a heurística anterior
    # (telemetria + HUD + piso mínimo de tempo, que já falhou uma vez em
    # produção: 2026-08-19 04:13, ~3s entre "engaged" e "chegada" por um
    # soluço visual do HUD). O Journal é a fonte autoritativa: validado a
    # 100% contra o histórico real desta sessão (21 chegadas legítimas + 2
    # quedas involuntárias, todas classificadas corretamente). Mais fiável
    # que a heurística, até prova em contrário.
    #
    # A flag SUPERCRUISE da telemetria continua a ser o gatilho -- é
    # instantânea, sem custo de I/O; só quando ela cai é que vale a pena ir
    # ler o Journal.
    print("A aguardar saída de Supercruise (telemetria + Journal)...")
    MAX_TENTATIVAS_FUGA = 3
    timeout_viagem = time.time() + 1500 # 25 mins

    while True:
        flags = ler_telemetria()

        if time.time() > timeout_viagem:
            abortar_com_erro("Timeout (25 mins). Viagem em supercruise excedeu o limite seguro.")

        saiu_supercruise = not bool(flags & STATUS_FLAGS["SUPERCRUISE"])
        if not saiu_supercruise:
            time.sleep(1)
            continue

        # Já saiu de Supercruise -- confirma pelo Journal. O evento pode
        # demorar um instante a ser escrito depois da flag mudar, por isso
        # espera curta em vez de decidir com o Journal ainda desatualizado.
        chegada_legitima = None
        limite_journal = time.time() + 10
        while time.time() < limite_journal:
            chegada_legitima = confirmar_chegada_por_journal()
            if chegada_legitima is not None:
                break
            time.sleep(0.5)

        if chegada_legitima:
            break  # confirmado -- sai do loop, segue para a sequência de chegada

        # Saiu do Supercruise mas o Journal não confirma chegada legítima
        # (False = SupercruiseEntry/StartJump antes de qualquer
        # DestinationDrop; None = o SupercruiseExit ainda nem apareceu no
        # Journal ao fim dos 10s -- trata como não confirmado, mais vale
        # reagir do que assumir chegada às cegas). O motivo exato não
        # interessa para a reação (o plano de fuga é o mesmo), só para o
        # log -- a flag INTERDICTION ajuda a distinguir depois.
        interdicao = bool(flags & STATUS_FLAGS["INTERDICTION"])
        msg = (f"Saída de Supercruise sem confirmação de chegada legítima pelo Journal "
               f"(journal={chegada_legitima}, interdição={'sim' if interdicao else 'nao confirmada'}) "
               f"-- a executar plano de fuga (tentativa {tentativas_fuga+1}/{MAX_TENTATIVAS_FUGA}).")
        print(f"\n[ALERTA] {msg}")
        _logger.error(msg)

        if tentativas_fuga >= MAX_TENTATIVAS_FUGA:
            abortar_com_erro(f"Plano de fuga já tentado {MAX_TENTATIVAS_FUGA}x sem conseguir retomar o "
                              f"Supercruise. Intervenção manual necessária.")

        # executar_plano_de_fuga() já trata do salto, da confirmação de
        # Supercruise e de ligar o Assist internamente -- ver docstring.
        executar_plano_de_fuga()
        return monitorar_viagem(tentativas_fuga + 1)

    # Chegada
    print("\n>>> CHEGADA CONFIRMADA! A executar travagem e boost...")
    falar("Dropping from supercruise.")

    time.sleep(2.0)
    pydirectinput.press('tab')
    time.sleep(15.0)
    # pydirectinput.press('tab')
    # time.sleep(15.0)
    pydirectinput.press('x')
    print(">>> Manobra concluída. A aguardar aproximação para docking.")

def executar():
    inicializar_infraestrutura()

    print("A iniciar em 1s...")
    time.sleep(1)

    # Mesma orquestração do plano de fuga: salta primeiro, só depois
    # estabiliza+liga o Assist+alinha -- não é preciso apontar a nada para
    # entrar em Supercruise (só para saltar para hyperspace/sair dela num
    # alvo específico é que precisa), e com o Assist já ligado o
    # alinhamento fica muito mais rápido e fiável do que tentar à mão
    # antes de sequer saltar.
    iniciar_salto_seguro()
    aguardar_supercruise_confirmado()

    print("\n>>> Em Supercruise -- a estabilizar (x) e a ligar o Assist...")
    pydirectinput.press('x')
    time.sleep(1.0)
    engatar_assistencia_menu()

    print(">>> Assist ligado -- a alinhar com o alvo...")
    _alinhar_com_olho("SUPERCRUISE - Ocular", limite_segundos=150.0)

    monitorar_viagem()

    if VISUAL_DEBUG:
        cv2.destroyAllWindows()

if __name__ == "__main__":
    executar()
