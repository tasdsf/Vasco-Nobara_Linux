import os
import sys
import time
import glob
import json
import logging
import cv2
import numpy as np
import pyttsx3
from infra_bridge import pydirectinput, gw, winsound, mss, print_ts as print, ED_LOG_DIR, ED_STATUS_FILE, capturar_screenshot_erro
import time

# ==========================================
# 0. LOGGING E INFRAESTRUTURA
# ==========================================
diretorio_atual = os.path.dirname(os.path.abspath(__file__))
pasta_logs = os.path.join(diretorio_atual, "logs")
log_test = os.path.join(pasta_logs, "undocking_test.png")
os.makedirs(pasta_logs, exist_ok=True)

# Logger proprio (nao usa logging.basicConfig -- com varios scripts no mesmo
# processo, so o primeiro basicConfig chamado ganha, e todos os outros ficam
# com o prefixo errado no log partilhado).
_logger = logging.getLogger("undocking")
# INFO por causa do registo do score do AUTO_COMPLETE (unico caso de uso
# nao-ERROR neste logger) -- ver aguardar_saida_estacao().
_logger.setLevel(logging.INFO)
if not _logger.handlers:
    _fh = logging.FileHandler(os.path.join(pasta_logs, "r2d2_combined.log"), encoding='utf-8')
    _fh.setFormatter(logging.Formatter('%(asctime)s - [UNDOCKING] - %(levelname)s - %(message)s'))
    _logger.addHandler(_fh)
    _logger.propagate = False

# Logger a parte, so para os scores do AUTO_COMPLETE -- enquanto o valor do
# template estiver "em estudo" (ver historico do threshold: 0.70 -> 0.73 ->
# 0.89 -> 0.84), queremos uma serie limpa de dados para decidir ate onde da
# para descer, sem ter de garimpar isto no meio do r2d2_combined.log. Regista
# a deteccao final que passou o threshold (2 campos: score,threshold) e
# tambem, em caso de timeout sem deteccao, o melhor score visto durante a
# espera (3 campos: score,threshold,TIMEOUT) -- sem isto nao havia como
# saber se a falha foi por pouco ou se o template nem chegou perto.
_logger_calibracao = logging.getLogger("undocking.auto_complete_calibracao")
_logger_calibracao.setLevel(logging.INFO)
if not _logger_calibracao.handlers:
    _fh_cal = logging.FileHandler(os.path.join(pasta_logs, "auto_complete_scores.log"), encoding='utf-8')
    _fh_cal.setFormatter(logging.Formatter('%(asctime)s,%(message)s'))
    _logger_calibracao.addHandler(_fh_cal)
    _logger_calibracao.propagate = False

def abortar_com_erro(mensagem):
    """ Regista o erro no log e dispara exit code 1 para o Orquestrador intercetar """
    print(f"\n[FATAL] {mensagem}")
    _logger.error(mensagem)
    capturar_screenshot_erro(pasta_logs)
    sys.exit(1)

NOME_JANELA = "R2D2 - Ocular de Auditoria"
VISUAL_DEBUG = False # Muda para False para esconder a janela

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
                pos_x = ecra_secundario["left"] + 50
                pos_y = ecra_secundario["top"] + 50
                cv2.moveWindow(NOME_JANELA, pos_x, pos_y)
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
# 1. SETUP DE GEOMETRIA E TEMPLATES
# ==========================================
MONITOR_MENU = {"top": 770, "left": 800, "width": 330, "height": 300}
MONITOR_CORNER = {"top": 50, "left": 1400, "width": 500, "height": 300}

LOG_DIR = ED_LOG_DIR

def get_latest_log():
    list_of_files = glob.glob(os.path.join(LOG_DIR, 'Journal.*.log'))
    if not list_of_files: return None
    return max(list_of_files, key=os.path.getctime)

def ler_telemetria_flags():
    """ Lê Flags do Status.json do jogo -- mesmo mecanismo do
    supercruise_assist.py. """
    try:
        with open(ED_STATUS_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
            return data.get("Flags", 0)
    except Exception:
        return 0

def obter_tamanho_atual_log():
    latest_log = get_latest_log()
    if not latest_log: return 0
    try:
        return os.path.getsize(latest_log)
    except:
        return 0

def ler_novos_eventos(posicao_ancora):
    latest_log = get_latest_log()
    if not latest_log: return []
    try:
        tamanho_atual = os.path.getsize(latest_log)
        if tamanho_atual <= posicao_ancora:
            return []
        with open(latest_log, 'r', encoding='utf-8') as f:
            f.seek(posicao_ancora)
            linhas_novas = f.readlines()
        eventos = []
        for linha in linhas_novas:
            try:
                data = json.loads(linha)
                if 'event' in data:
                    eventos.append(data)
            except: continue
        return eventos
    except Exception as e:
        print(f"[ERRO] Falha ao ler stream de logs: {e}")
        return []

pasta_imagens = os.path.join(diretorio_atual, 'images')

templates_nomes = {
    'repair': 'repair.png',
    'autolaunch': 'AUTO_LAUNCH.png',
    'autolaunch_carrier': 'AUTO_LAUNCH_CARRIER.png',
    'noselection': 'NO_SELECTION.png',
    # Carrier tem o mesmo menu mas com "CARRIER SERVICES" em vez de "STARPORT
    # SERVICES" -- o texto diferente basta para o match cair de ~90% para
    # ~68% (abaixo do threshold de 70%) e abortar em falso. Fallback abaixo.
    'noselection_carrier': 'NO_SELECTION_CARRIER.png',
    'auto_complete': 'AUTO_LAUNCH_COMPLETE.png',
    # Ícone de munições/heatsinks na fila de ícones do topo do menu --
    # 'no_ammo' (ícone apagado) é o sinal para ir repor antes de descolar
    # (ver Passo 0b) -- sem heatsinks para repor, o 'v' do plano de fuga
    # (supercruise_assist.py) fica sem efeito quando for preciso.
    'ammo': 'ammo.png',
    'no_ammo': 'no_ammo.png',
    # Mesmo ícone apagado/aviso da fila do topo do menu, mas para
    # reparação -- mesmo tratamento do no_ammo (ver Passo 1b), só que
    # com o seu próprio 1x 'd' + space em vez de partilhar o 2x do ammo.
    'need_repair': 'NEED-REPAIR.png'
}

# Se False, ignora o template 'need_repair' e volta ao comportamento
# anterior (só no_ammo, com 2x 'd' + space -- cobre às cegas por um
# eventual aviso de reparação não verificado explicitamente). Serve de
# válvula de escape caso o template novo dê problemas.
VERIFICAR_NEED_REPAIR = True

templates = {}
try:
    for chave, nome_arq in templates_nomes.items():
        caminho = os.path.join(pasta_imagens, nome_arq)
        img = cv2.imread(caminho, cv2.IMREAD_COLOR)
        if img is None: raise FileNotFoundError(f"Falta a imagem: {caminho}")
        templates[chave] = img
    print(f"[SISTEMA] Módulo: Undocking Ótico Closed-Loop Carregado.")
except Exception as e:
    abortar_com_erro(f"Falha de Assinatura Visual ao arrancar: {e}")

# --- Motor de Voz ---
engine = pyttsx3.init()
def falar(texto):
    print(f"[VOZ] {texto}")
    engine.say(texto)
    engine.runAndWait()

# ==========================================
# 2. MOTOR DE VISÃO COMPUTACIONAL
# ==========================================
def procurar_template(template, nome_label, monitor, threshold=0.85):
    if template is None: return False, 0.0

    with mss.mss() as sct:
        monitors = sct.monitors
        try:
            monitor_jogo = monitors[1]
        except IndexError:
            monitor_jogo = monitors[0]

        area_real = {
            "top": monitor_jogo["top"] + monitor["top"],
            "left": monitor_jogo["left"] + monitor["left"],
            "width": monitor["width"],
            "height": monitor["height"]
        }

        img_bgra = np.array(sct.grab(area_real))
        img_bgr = cv2.cvtColor(img_bgra, cv2.COLOR_BGRA2BGR)
        # teste begin
        cv2.imwrite(log_test, img_bgr)
        # teste end
        resultado = cv2.matchTemplate(img_bgr, template, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, max_loc = cv2.minMaxLoc(resultado)

        encontrou = max_val >= threshold

        if VISUAL_DEBUG:
            cor = (0, 255, 0) if encontrou else (0, 0, 255)
            if encontrou:
                h, w = template.shape[:2]
                cv2.rectangle(img_bgr, max_loc, (max_loc[0] + w, max_loc[1] + h), cor, 2)

            cv2.rectangle(img_bgr, (0, 0), (350, 45), (0, 0, 0), -1)
            cv2.putText(img_bgr, f"{nome_label}: {max_val*100:.1f}% (Min: {threshold*100:.0f}%)", (10, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)

            img_show = cv2.resize(img_bgr, (700, 600))
            cv2.imshow(NOME_JANELA, img_show)
            cv2.waitKey(1)

        return encontrou, max_val

# ==========================================
# 3. MÁQUINA DE ESTADOS DETERMINÍSTICA
# ==========================================
def executar_auto_launch():
    print("\n==================================================")
    print(">>> CODE EXECUTION: MANOBRA DE UNDOCKING SEQUENCIAL")
    print("==================================================")

    # Passo 0: Aquecer a captura de ecrã -- ANTES de qualquer tecla. A
    # sessão PipeWire é lazy (infra_bridge._garantir_sessao) e só arranca
    # no primeiro sct.grab() de todo o processo; se undocking.py for o
    # primeiro script a ler o ecrã numa execução (ex: vasco.py a retomar
    # direto nesta etapa), esse primeiro grab dispara o popup do KDE a
    # pedir confirmação -- um passo manual que demora um tempo
    # imprevisível. Sem este aquecimento aqui, o Passo 1 (3x 'w' + space,
    # às cegas, sem leitura de ecrã nenhuma) disparava ANTES do popup ser
    # confirmado -- teclas enviadas para um menu que podia nem estar
    # estabilizado ainda, e o Passo 1b só lia o ecrã bem mais tarde,
    # depois do utilizador confirmar o popup (confirmado em produção
    # 2026-08-21 ~00:06). O resultado do match aqui é ignorado -- só
    # interessa forçar a sessão a arrancar e o popup a ser respondido já.
    procurar_template(templates['repair'], "AQUECIMENTO_CAPTURA", MONITOR_MENU, 0.85)

    # Passo 0c: Deteção -- ANTES de qualquer tecla. need_repair.png exige
    # a gota (combustível) E a chave-inglesa AMBAS acesas na fila de
    # ícones -- a gota está SEMPRE acesa ao aterrar (gastámos combustível
    # a chegar), por isso esta deteção só é válida no estado ORIGINAL da
    # fila. Se detetássemos depois do Passo 1 (que confirma o
    # abastecimento), a gota já teria mudado de estado e o need_repair
    # deixava de bater -- confirmado em produção (2026-08-21): a gota era
    # validada mas o need_repair falhava logo a seguir, exatamente por
    # isto. Por isso agora: deteta tudo primeiro, decide a sequência de
    # teclas, só depois mexe em alguma coisa.
    if VERIFICAR_NEED_REPAIR:
        precisa_reparar, score_reparar = procurar_template(templates['need_repair'], "NEED_REPAIR", MONITOR_MENU, 0.85)
    else:
        precisa_reparar, score_reparar = False, 0.0
    sem_ammo, score_ammo = procurar_template(templates['no_ammo'], "NO_AMMO", MONITOR_MENU, 0.85)

    # Passo 0d: Confirma que estamos mesmo no ecrã certo (a fila de ícones
    # fuel/repair/ammo) antes de avançar às cegas com o 3x 'w' + space.
    # noselection/noselection_carrier só ficam válidos DEPOIS da
    # navegação (3x 'w') -- não servem aqui, ainda antes de qualquer
    # tecla. A validação correta neste ponto é: NEED_REPAIR (chave-
    # inglesa+gota em laranja, já detetado no Passo 0c) OU repair.png
    # (chave-inglesa cinza/branca, estilo STARPORT SERVICES sem dano). Se
    # nenhum dos dois bater, não há garantia nenhuma de estarmos no menu
    # certo -- aborta em vez de mandar teclas para o ecrã errado.
    if not precisa_reparar:
        repair_normal_ok, score_nao_reparar = procurar_template(templates['repair'], "REPAIR (validação ecrã)", MONITOR_MENU, 0.80)
        if not repair_normal_ok:
            abortar_com_erro(f"Nem NEED-REPAIR nem repair.png detetados ({score_nao_reparar*100:.1f}%) ecrã errado, a rotina não deve prosseguir às cegas.")

    # Passo 1: Subida Mecânica + Abastecimento -- corre logo a seguir,
    # sem esperar pelo repair.png primeiro. repair.png é o ícone da
    # chave-inglesa no estilo branco/cinza contornado, que só bate bem
    # DEPOIS de tratar fuel/repair/ammo (Passo 1b) -- no menu "CARRIER
    # SERVICES" o ícone aparece antes em laranja preenchido (mesmo estilo
    # do NEED-REPAIR.png) e o repair.png não bate (testado: 0.799, abaixo
    # do threshold 0.85). Ver Passo 1c mais abaixo, onde repair.png passa
    # a confirmar depois. O 'space' final confirma sempre o combustível
    # (a gota, primeiro ícone da fila, sempre ativa ao aterrar).
    time.sleep(0.5)
    print("\nA enviar comandos mecânicos: 3x 'w' + 1x 'space' (combustível)...")
    for _ in range(3):
        pydirectinput.press('w')
        time.sleep(0.2)
    pydirectinput.press('space')
    time.sleep(0.3)

    # Passo 1b: Reparação e Ammo -- com base na deteção do Passo 0c. Cada
    # 'd' avança um ícone na fila (fuel -> repair -> ammo); sem
    # necessidade de reparar, são precisos 2x 'd' seguidos para saltar
    # diretamente do fuel para o ammo. Sem isto, o 'v' (heatsink) do
    # plano de fuga (supercruise_assist.py) fica sem efeito na próxima
    # interdição. Com VERIFICAR_NEED_REPAIR=False, precisa_reparar nunca
    # é True, e ammo sozinho usa sempre o caminho de 2x 'd' + space
    # (cobre às cegas por um eventual aviso de reparação não verificado).
    if precisa_reparar:
        print(f"\nNecessita reparação ({score_reparar*100:.1f}%) -- a confirmar (1x 'd' + space)...")
        # 'd' aqui é a PRIMEIRA interação com a fila de ícones (cursor
        # começa no fuel, repair é +1 'd'). Testado em produção
        # (2026-08-21): com 0.2s entre 'd' e 'space', o space confirmava
        # fuel em vez de repair -- o cursor parece não ter tido tempo de
        # mudar de ícone ainda. Intervalo maior aqui.
        pydirectinput.press('d')
        time.sleep(0.4)
        pydirectinput.press('space')
        time.sleep(0.5)

        if sem_ammo:
            print(f"\nSem stock de Ammo/Heatsinks ({score_ammo*100:.1f}%) -- a repor (1x 'd' + space)...")
            pydirectinput.press('d')
            time.sleep(0.2)
            pydirectinput.press('space')
            time.sleep(0.5)
        else:
            # Sem ação de ammo a seguir -- a sequência terminaria mesmo em
            # cima do ícone de reparação, que fica "iluminado"/selecionado
            # e impede a validação de NO_SELECTION mais à frente (Passo
            # 2). Ao contrário do ammo (que já terminava a fila sem este
            # problema antes desta mudança), o repair fica preso
            # selecionado -- um 'd' extra tira o foco de lá.
            print("\nA sair do ícone de reparação (1x 'd') para permitir NO_SELECTION...")
            pydirectinput.press('d')
            time.sleep(0.3)
    elif sem_ammo:
        print(f"\nSem stock de Ammo/Heatsinks ({score_ammo*100:.1f}%) -- a repor (2x 'd' + space)...")
        pydirectinput.press('d')
        time.sleep(0.2)
        pydirectinput.press('d')
        time.sleep(0.2)
        pydirectinput.press('space')
        time.sleep(0.5)

    # Passo 1c: Estabilização do HUD do Menu -- movido para depois do
    # Passo 1b (era Passo 0, antes de tudo). repair.png só serve de
    # referência fiável depois de fuel/repair/ammo estarem tratados (ver
    # nota no Passo 1).
    print("\nA aguardar estabilização do menu...")
    timeout_menu = time.time() + 15  # Watchdog de 15 segundos
    while True:
        if time.time() > timeout_menu:
            falar("Error. Interface stabilization timeout.")
            abortar_com_erro("Timeout (15s) à espera que o botão 'Repair' estabilize no menu da estação.")

        m1, _ = procurar_template(templates['repair'], "ESTABILIZACAO", MONITOR_MENU, 0.80)
        if m1: break
        time.sleep(0.3)

    time.sleep(0.5)

    # Passo 2: Validação Cega
    print("\nA validar 'NO_SELECTION' no topo do menu...")
    time.sleep(0.3)
    noselect_val = 0.7
    sucesso_idle, score_idle = procurar_template(templates['noselection'], "VAL_NO_SELECTION", MONITOR_MENU, noselect_val)
    if not sucesso_idle:
        # Fallback: pode ser o menu do Fleet Carrier ("CARRIER SERVICES" em
        # vez de "STARPORT SERVICES") -- mesmo layout, texto diferente.
        sucesso_idle_carrier, score_idle_carrier = procurar_template(templates['noselection_carrier'], "VAL_NO_SELECTION_CARRIER", MONITOR_MENU, noselect_val)
        if sucesso_idle_carrier:
            print(f"[OK] 'NO_SELECTION' (Carrier) validado com {score_idle_carrier*100:.1f}%.")
        else:
            falar("Error. Validation failed at menu top. Aborting sequence.")
            abortar_com_erro(f"Falha crítica ótica no teto. Match real: {score_idle*100:.1f}% / Carrier: {score_idle_carrier*100:.1f}% (Exigia: {noselect_val*100}%)")
    else:
        print(f"[OK] 'NO_SELECTION' validado com {score_idle*100:.1f}%.")

    # Passo 3: Descida Mecânica
    print("\nA navegar para a posição do botão: 2x 's'...")
    for _ in range(2):
        pydirectinput.press('s')
        time.sleep(0.25)

    # Passo 4: Validação do Alvo
    print("\nA auditar foco do botão Auto-Launch...")
    time.sleep(0.3)
    autolaunch_val = 0.82
    sucesso_al, score_al = procurar_template(templates['autolaunch'], "VAL_AUTO_LAUNCH", MONITOR_MENU, autolaunch_val)
    template_al_ativo = templates['autolaunch']
    if not sucesso_al:
        # Fallback: mesmo mismatch do Passo 2 -- "CARRIER SERVICES" em vez de
        # "STARPORT SERVICES" por trás da linha "Auto Launch" já destacada.
        sucesso_al_carrier, score_al_carrier = procurar_template(templates['autolaunch_carrier'], "VAL_AUTO_LAUNCH_CARRIER", MONITOR_MENU, autolaunch_val)
        if sucesso_al_carrier:
            print(f"[OK] 'AUTO_LAUNCH' (Carrier) validado com {score_al_carrier*100:.1f}%.")
            template_al_ativo = templates['autolaunch_carrier']
        else:
            falar("Auto launch not detected.")
            abortar_com_erro(f"Botão Auto-Launch não detetado (Match real: {score_al*100:.1f}% / Carrier: {score_al_carrier*100:.1f}% / Exigia {autolaunch_val*100}%)")

    # Passo 5: Execução Limpa, com confirmação de que o press saiu do botão.
    # O press('space') é um subprocess.run ao ydotool sem verificar o codigo
    # de saida -- se falhar em silencio nesse instante, o botao continua
    # visivel e nunca saberiamos. Por isso confirmamos que ele desapareceu
    # (sinal de que o menu reagiu) antes de avançar para a espera longa.
    # Usa o mesmo template (estação ou carrier) que validou no Passo 4 --
    # senão a confirmação de "desapareceu" fica a comparar com o template
    # errado outra vez.
    print("\n>>> TUDO VALIDADO! A disparar comando SPACE...")
    for tentativa in range(3):
        pydirectinput.press('space')
        time.sleep(1.0)
        ainda_visivel, _ = procurar_template(template_al_ativo, "VAL_AUTO_LAUNCH_POS", MONITOR_MENU, autolaunch_val)
        if not ainda_visivel:
            break
        print(f"[AVISO] Botão Auto-Launch ainda visível após o SPACE (tentativa {tentativa+1}/3). A repetir...")
    else:
        print("[AVISO] Auto-Launch pode não ter disparado após 3 tentativas -- a prosseguir mesmo assim.")

    return True

AUTO_COMPLETE_THRESHOLD = 0.84  # valor defensivo enquanto calibramos -- ver logs/auto_complete_scores.log

def aguardar_saida_estacao():
    print("\n>>> FASE: Detetar saída da estação...")
    print("[VISÃO] A monitorizar o HUD para a notificação 'AUTO LAUNCH COMPLETE'...")

    # Watchdog de 9 Minutos (A estação pode ter fila de trânsito) -- mesmo
    # teto que aguardar_confirmacao_docking() usa no docking.py (540s);
    # 180s (3 min) era curto demais e dava timeout em falso com a nave
    # ainda genuinamente em fila.
    timeout_saida = time.time() + 540
    melhor_score = 0.0  # maior score visto nesta espera, mesmo que nunca cruze o threshold

    while True:
        if time.time() > timeout_saida:
            # Falha: nunca cruzou o threshold. Regista o melhor score visto
            # para sabermos se foi "quase" ou se o template nem chegou perto.
            _logger_calibracao.info(f"{melhor_score*100:.2f},{AUTO_COMPLETE_THRESHOLD*100:.0f},TIMEOUT")
            falar("Warning. Auto launch timeout exceeded.")
            abortar_com_erro("Timeout (540s) à espera de sair da estação. A nave está presa no trânsito?")

        encontrou, score = procurar_template(templates['auto_complete'], "AUTO_COMPLETE", MONITOR_CORNER, AUTO_COMPLETE_THRESHOLD)
        melhor_score = max(melhor_score, score)
        if encontrou:
            print(f"\n>>> [VISÃO] Notificação detetada com {score*100:.1f}% de precisão!")
            _logger.info(f"AUTO_COMPLETE detetado com {score*100:.1f}% de precisao (threshold {AUTO_COMPLETE_THRESHOLD*100:.0f}%).")
            _logger_calibracao.info(f"{score*100:.2f},{AUTO_COMPLETE_THRESHOLD*100:.0f}")
            print("[LOG] Saída da estação confirmada.")
            break
        time.sleep(0.4)

    print("[SUCESSO] Estamos no espaço aberto!")
    falar("Auto launch terminated commander.")

def sequencia_salto():
    # Sem 'x' aqui -- este código é de quando o OLHO corria logo a seguir
    # ao undocking e precisava da nave parada para alinhar (arquitetura
    # antiga). Agora o alinhamento passou para dentro do
    # supercruise_assist.py, corrido só depois de entrar em Supercruise
    # (ver _alinhar_com_olho) -- não faz sentido parar aqui só para voltar
    # a acelerar a seguir; o 'x' só deve acontecer logo a seguir a ENTRAR
    # em Supercruise.
    print("\n>>> FASE: Impulso de Saída...")
    pydirectinput.keyDown('.')
    time.sleep(5)
    pydirectinput.keyUp('.')
    pydirectinput.press('tab')
    time.sleep(15.0)

FSD_MASS_LOCKED_FLAG = 0x10000
MASS_LOCK_CONFIRMACOES = 2  # deteções consecutivas exigidas antes de aceitar o sinal (evita 1 leitura a meio da escrita do ficheiro)

def aguardar_no_fire_zone_exit(ancora_log, timeout=60):
    """ Gate final antes de entregar o controlo ao OLHO: a deteção visual do
    AUTO_COMPLETE (acima) pode dar falso positivo -- já aconteceu a nave ficar
    presa junto ao pad com o HUD a bater ruído nos 70%+ e a manobra seguir em
    frente na mesma. Este evento vem do próprio Journal do jogo, por isso não
    há como fingir: só avançamos quando o jogo confirma "No fire zone exited".

    Redundância: já aconteceu o Journal simplesmente parar de escrever
    eventos a meio da manobra (sessão presa) e este gate nunca confirmar,
    mesmo com a nave já fora da no-fire-zone. Por isso também aceitamos a
    flag FSD_MASS_LOCKED da telemetria (Status.json) a desligar-se -- sinal
    de estado em tempo real e autoritativo (o próprio jogo, sem depender de
    OCR/visão), por isso não tem o mesmo risco de falso positivo do
    AUTO_COMPLETE. (Chegámos a usar um checkbox "MASS LOCKED" do HUD por
    visão para o mesmo efeito -- a telemetria é o mesmo sinal, direto da
    fonte, sem depender de captura de ecrã.) Exige duas leituras seguidas
    para não confiar numa única leitura a meio da escrita do ficheiro.

    Se a nave estiver mesmo presa em trânsito (raro) e nenhum dos dois sinais
    confirmar, abortamos como qualquer outra falha desta máquina de estados
    -- não vale a pena complicar com lógica de recuperação para um caso raro;
    aceitar o prejuízo e deixar o 'a' (modo automático) tentar de novo é mais
    barato. """
    print(f"\n>>> FASE: A confirmar saída da no-fire-zone via Journal + Telemetria (timeout {timeout}s)...")
    timeout_real = time.time() + timeout
    confirmacoes_mass_lock = 0

    while time.time() < timeout_real:
        for evento in ler_novos_eventos(ancora_log):
            if evento.get('event') == 'ReceiveText' and evento.get('Message') == '$STATION_NoFireZone_exited;':
                print("[OK] 'No fire zone exited' confirmado pelo Journal.")
                _logger.info("No fire zone exited confirmado (Journal) -- handoff para OLHO autorizado.")
                return True

        flags = ler_telemetria_flags()
        if not bool(flags & FSD_MASS_LOCKED_FLAG):
            confirmacoes_mass_lock += 1
            if confirmacoes_mass_lock >= MASS_LOCK_CONFIRMACOES:
                print("[OK] 'No fire zone exited' confirmado pela telemetria (FSD_MASS_LOCKED desligado) -- Journal não confirmou a tempo.")
                _logger.info("No fire zone exited confirmado (Telemetria FSD_MASS_LOCKED) -- handoff para OLHO autorizado.")
                return True
        else:
            confirmacoes_mass_lock = 0

        time.sleep(0.5)

    falar("Warning. Still inside station no fire zone.")
    abortar_com_erro("Timeout à espera de 'No fire zone exited' no Journal/Telemetria. A nave pode estar presa/bloqueada perto da estação.")

# ==========================================
# 4. EXECUÇÃO PRINCIPAL
# ==========================================
def executar():
    inicializar_infraestrutura()

    print("O R2D2 assume os comandos em 1 segundos...")
    time.sleep(1)

    ancora_log = obter_tamanho_atual_log()

    sucesso_execucao = executar_auto_launch()
    if sucesso_execucao:
        aguardar_saida_estacao()
        sequencia_salto()
        aguardar_no_fire_zone_exit(ancora_log)

    if VISUAL_DEBUG:
        cv2.destroyAllWindows()

if __name__ == "__main__":
    executar()
