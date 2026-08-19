import os
import sys
import time
import glob
import json
import logging
import cv2
import numpy as np
import pyttsx3
from infra_bridge import pydirectinput, gw, winsound, mss
import time

if sys.platform == "win32":
    import winsound
    def tocar_alarme_erro():
        winsound.Beep(1200, 300)
else:
    import os
    def tocar_alarme_erro():
        # Usa o beep do sistema ou simplesmente print (ou ignora)
        print("\a") # Carácter ASCII de Bell (emite um som no terminal se configurado)

# ==========================================
# 0. LOGGING E INFRAESTRUTURA
# ==========================================
diretorio_atual = os.path.dirname(os.path.abspath(__file__))
pasta_logs = os.path.join(diretorio_atual, "logs")
os.makedirs(pasta_logs, exist_ok=True)

# Logger proprio (nao usa logging.basicConfig -- com varios scripts no mesmo
# processo, so o primeiro basicConfig chamado ganha, e todos os outros ficam
# com o prefixo errado no log partilhado).
_logger = logging.getLogger("docking")
_logger.setLevel(logging.ERROR)
if not _logger.handlers:
    _fh = logging.FileHandler(os.path.join(pasta_logs, "r2d2_combined.log"), encoding='utf-8')
    _fh.setFormatter(logging.Formatter('%(asctime)s - [DOCKING] - %(levelname)s - %(message)s'))
    _logger.addHandler(_fh)
    _logger.propagate = False

# Motor de Voz e Som
engine = pyttsx3.init()
def falar(texto):
    print(f"[VOZ] {texto}")
    engine.say(texto)
    engine.runAndWait()

#def tocar_alarme_erro():
#    """ Toca um beep duplo agudo para chamar a atenção do piloto """
#    winsound.Beep(1200, 300)
#    time.sleep(0.1)
#    winsound.Beep(1200, 600)

def abortar_com_erro(mensagem):
    print(f"\n[FATAL] {mensagem}")
    _logger.error(mensagem)
    tocar_alarme_erro()
    falar("Critical error during docking sequence. Manual intervention required.")
    # É ESTE sys.exit(1) QUE AVISA O VASCO.PY QUE HOUVE UMA FALHA!
    sys.exit(1)

NOME_JANELA = "Ocular do Bot - Docking"
VISUAL_DEBUG = False

def inicializar_infraestrutura():
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
# 1. SETUP E CAMINHOS
# ==========================================
MONITOR_PANEL = {"top": 250, "left": 200, "width": 1200, "height": 450}
from infra_bridge import ED_LOG_DIR
LOG_DIR = ED_LOG_DIR

pasta_imagens = os.path.join(diretorio_atual, 'images')
templates_nomes = {
    'contacts_tab': 'CONTACTS.png',
    'docking_off': 'REQUEST_DOCKING_OFF.png',
    'docking_on': 'REQUEST_DOCKING_ON.png',
    'repair': 'repair.png'
}

templates = {}
try:
    for chave, nome_arq in templates_nomes.items():
        caminho = os.path.join(pasta_imagens, nome_arq)
        img = cv2.imread(caminho, cv2.IMREAD_COLOR)
        if img is None: raise FileNotFoundError(f"Falta imagem: {caminho}")
        templates[chave] = img
    print(f"[SISTEMA] Módulo Docking Automático Blindado carregado.")
except Exception as e:
    abortar_com_erro(f"Falha ao carregar imagens para a memória: {e}")

# ==========================================
# 2. MOTOR DE VISÃO
# ==========================================
def procurar_template(template, nome_label, threshold=0.80):
    with mss.mss() as sct:
        img_bgra = np.array(sct.grab(MONITOR_PANEL))
        img_bgr = cv2.cvtColor(img_bgra, cv2.COLOR_BGRA2BGR)
        resultado = cv2.matchTemplate(img_bgr, template, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, max_loc = cv2.minMaxLoc(resultado)
        encontrou = max_val >= threshold
        
        if VISUAL_DEBUG:
            cor = (0, 255, 0) if encontrou else (0, 0, 255)
            if encontrou:
                h, w = template.shape[:2]
                cv2.rectangle(img_bgr, max_loc, (max_loc[0] + w, max_loc[1] + h), cor, 2)
            cv2.rectangle(img_bgr, (5, 5), (450, 80), (0, 0, 0), -1)
            cv2.putText(img_bgr, f"Alvo: {nome_label}", (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
            cv2.putText(img_bgr, f"Match: {max_val:.2f} / {threshold:.2f}", (10, 65), cv2.FONT_HERSHEY_SIMPLEX, 0.8, cor, 2)
            cv2.imshow(NOME_JANELA, img_bgr)
            cv2.waitKey(1)
            
        return encontrou

# ==========================================
# 3. MOTOR DE LOGS COM CURSOR DINÂMICO
# ==========================================
def get_latest_log():
    list_of_files = glob.glob(os.path.join(LOG_DIR, 'Journal.*.log'))
    if not list_of_files: return None
    return max(list_of_files, key=os.path.getctime)

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

# ==========================================
# 4. VERIFICAÇÃO EM TEMPO REAL (CLOSED-LOOP)
# ==========================================
def aguardar_confirmacao_docking(posicao_ancora, timeout=540):
    print(f"[DOCKING] A monitorizar aproximação e pouso (Timeout Máximo: {timeout}s)...")
    timeout_real = time.time() + timeout
    
    while time.time() < timeout_real:
        novos = ler_novos_eventos(posicao_ancora)
        
        for evento in novos:
            nome_evento = evento.get('event')
            if nome_evento == 'Docked':
                print(f"[OK] Pouso confirmado via Log: Concluído em {evento.get('StationName', 'Estação')}")
                return True
            elif nome_evento == 'DockingCancelled' or nome_evento == 'DockingDenied':
                print(f"[ALERTA] A permissão de atracagem foi revogada ou negada pelo jogo.")
                
                return False

        template = templates.get('repair')
        if template is not None:
            if procurar_template(template, "Menu Repair (Fallback)", 0.85):
                print("[OK] Menu visual 'Repair' detetado! Atracagem bem-sucedida.")
                return True
                
        time.sleep(1.0)
        
    print("[ALERTA] Timeout esgotado à espera do término do docking.")
    return False

# ==========================================
# 5. ROTINA CRÍTICA DE EXECUÇÃO
# ==========================================
def solicitar_docking():
    print("\n[VÔO] Parando nave (X)...")
    pydirectinput.press('x')
    time.sleep(0.5)

    timeout_global = time.time() + 180 

    # --- NOVO SISTEMA COM 3 TENTATIVAS MÁXIMAS ---
    for tentativa in range(1, 4):
        if time.time() > timeout_global:
            abortar_com_erro("Timeout global (180s) excedido. A abortar operação.")

        print(f"\n>>> [TENTATIVA {tentativa}/3] Abrindo painel lateral (1)...")
        pydirectinput.press('1')
        time.sleep(1.2)
        
        aba_encontrada = False
        for _ in range(6):
            if procurar_template(templates['contacts_tab'], "CONTACTS", 0.65):
                print("[LOG] Aba Contacts confirmada!")
                aba_encontrada = True
                break
            pydirectinput.press('e')
            time.sleep(0.6)
        
        if not aba_encontrada:
            msg = "[ERRO] Não detetei a aba Contacts. Tentando reiniciar ciclo..."
            print(msg)
            logging.warning(msg)
            pydirectinput.press('1')
            time.sleep(2)
            continue 

        # Seleciona a Estação na lista
        pydirectinput.press('space')
        time.sleep(0.8)
        
        clicou = False
        ancora_log = 0
        for _ in range(5):
            if procurar_template(templates['docking_on'], "DOCKING ON", 0.65):
                ancora_log = obter_tamanho_atual_log()
                pydirectinput.press('space')
                clicou = True
                break
            if procurar_template(templates['docking_off'], "DOCKING OFF", 0.65):
                pydirectinput.press('d')
                time.sleep(0.4)
        
        # 'backspace' em vez de '1' -- '1' é o toggle que abriu o painel, só
        # fecha se o jogo estiver exatamente no estado que o toggle espera;
        # 'backspace' sai de qualquer nível de menu, mesmo padrão já usado no
        # select_target.py.
        pydirectinput.press('backspace') # Fecha o painel holográfico
        time.sleep(1.5)

        if clicou:
            # Pedido enviado: a partir daqui a nave pode entrar em manobras
            # automáticas de aproximação (acelerações/travagens assim que o
            # DockingComputer assume) que tornam qualquer template visual
            # pouco fiável -- e reabrir o painel para tentar confirmar
            # visualmente arrisca acertar sem querer no botão de pedido outra
            # vez, o que aborta o docking a meio do percurso e deixa a nave
            # parada até intervenção manual. Por isso, uma vez enviado o
            # pedido, não se manda mais nenhum input: só se monitoriza o
            # journal (Granted/Denied/Cancelled/Docked) até ao fim, em vez de
            # reiniciar a tentativa ao fim de uma janela curta de espera.
            print("[LOG] Pedido enviado. A monitorizar o journal até ao pouso (sem mais inputs)...")
            if aguardar_confirmacao_docking(ancora_log):
                print("\n[SUCESSO] Operação de docking totalmente finalizada.")
                return True
            else:
                abortar_com_erro("Pedido de docking enviado mas não confirmado (negado, cancelado ou "
                                  "timeout à espera do pouso). Intervenção manual necessária.")
        else:
            msg = "[ERRO] Botão de request não foi localizado."
            print(msg)
            logging.warning(f"Tentativa {tentativa} falhou: Botão Request Docking ausente.")
            falar("Request button not found.")
            time.sleep(2)

    # --- SE O CICLO FOR TERMINAR AS 3 TENTATIVAS SEM RETORNAR SUCESSO ---
    abortar_com_erro("Esgotadas as 3 tentativas de Docking. A estação não responde ou o menu está dessincronizado.")

def executar():
    inicializar_infraestrutura()

    print("Bot pronto. Inicia a aproximação em 1 segundos...")
    time.sleep(1)
    solicitar_docking()

    if VISUAL_DEBUG:
        cv2.destroyAllWindows()

if __name__ == "__main__":
    executar()
