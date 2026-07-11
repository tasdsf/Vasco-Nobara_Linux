import os
import sys
import subprocess
import logging
from logging.handlers import RotatingFileHandler
import datetime
import time

# Mapeamento estrito dos scripts aos números de 1 a 8
SCRIPTS = {
    "1": "comprar.py",
    "2": "vender.py",
    "3": "select_target.py",
    "4": "undocking.py",
    "5": "olho.py",
    "6": "supercruise_assist.py",
    "7": "docking.py"
}

LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
os.makedirs(LOG_DIR, exist_ok=True)

# Nome fixo do ficheiro de log (append)
LOG_FILE = os.path.join(LOG_DIR, "r2d2_combined.log")

def configurar_logger():
    logger = logging.getLogger("r2d2_main_logger")
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        # FileHandler simples em modo append
        fh = logging.FileHandler(LOG_FILE, mode="a", encoding="utf-8")
        fh.setLevel(logging.INFO)
        formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
        fh.setFormatter(formatter)
        logger.addHandler(fh)

        # Opcional: adicionar também um handler rotativo
        # rot = RotatingFileHandler(LOG_FILE, mode="a", maxBytes=10*1024*1024, backupCount=5, encoding="utf-8")
        # rot.setLevel(logging.INFO)
        # rot.setFormatter(formatter)
        # logger.addHandler(rot)

        # Evita que mensagens subam para o root logger e sejam duplicadas
        logger.propagate = False
    return logger

def exibir_menu():
    os.system('cls' if os.name == 'nt' else 'clear')
    print("="*45)
    print("      R2D2 - COCKPIT DE AUTOMACAO -")
    print("="*45)
    print("1 - comprar.py")
    print("2 - vender.py")
    print('==== move to target ======')
    print("\n>>move to target (Execução via PowerShell com Auto-Retorno):")
    print("3 - select_target.py")
    print("4 - undocking.py")
    print("5 - olho.py")
    print("6 - supercruise_assist.py")
    print("7 - docking.py")
    print("\n" + "-"*45)
    print("0 - SAIR")
    print("-" * 45)

def executar_script_streaming(script_name):
    script_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), script_name)
    if not os.path.exists(script_path):
        print(f"\n[ERRO] Alvo falhou: O ficheiro '{script_name}' não existe.")
        input("\nPressione Enter para continuar...")
        return

    logger = configurar_logger()

    print(f"\n[POWERSHELL] Passando controlo para: {script_name}")
    print(f"[INFO] Log em: {LOG_FILE}")
    print("[INFO] O menu irá retornar assim que encerrar o script.")
    print("-" * 50)

    # Usa o mesmo interpretador Python que está a correr este script
    cmd = [sys.executable, script_path]

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            universal_newlines=True
        )

        # Leitura linha a linha em tempo real
        with proc.stdout:
            for line in iter(proc.stdout.readline, ""):
                if line == "" and proc.poll() is not None:
                    break
                # Remove nova linha final para evitar linhas em branco duplas
                stripped = line.rstrip("\n")
                logger.info(stripped)
                print(stripped)
        returncode = proc.wait()
        if returncode != 0:
            logger.error(f"Script terminou com código de saída {returncode}")
            print(f"\n[AVISO] Script {script_name} terminou com código de saída {returncode}. Ver log.")
        else:
            logger.info("Script concluído com sucesso")
    except KeyboardInterrupt:
        # Tenta terminar o processo filho de forma limpa
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            pass
        logger.warning("Execução interrompida pelo utilizador (KeyboardInterrupt)")
        print("\n[AVISO] Execução interrompida pelo utilizador.")
    except Exception as e:
        logger.exception("Erro ao executar o script")
        print(f"\n[ERRO] Exceção durante execução: {e}")

    print("\n" + "-" * 50)
    print("[SISTEMA] Script concluído. Retornando ao menu principal...")
    time.sleep(1.5)

def main():
    while True:
        exibir_menu()
        escolha = input("Escolha > ").strip()
        if escolha == '0':
            print("\nDesligando painel de controlo. Boa caça, Comandante.")
            break
        elif escolha in SCRIPTS:
            executar_script_streaming(SCRIPTS[escolha])
        else:
            print("\n[AVISO] Código inválido. Introduz um número de 1 a 8.")
            time.sleep(1)

if __name__ == "__main__":
    main()
