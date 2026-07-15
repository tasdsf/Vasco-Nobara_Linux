# infra_bridge.py
"""
Camada de abstração de plataforma para o projeto R2D2 / Elite Dangerous.
Target: Linux Nobara, KDE Plasma 6, Wayland, Steam/Proton.

Exports públicos:
    pydirectinput   - input injection (pyautogui)
    gw              - window focus (ydotool -> xdotool -> wmctrl)
    winsound        - audio beep (paplay -> aplay -> bell)
    mss             - screen capture (PipeWire via xdg-desktop-portal-kde)
    keyboard        - hotkey listener (requer grupo 'input')
    print_ts        - print com timestamp [HH:MM:SS] -- importar como
                      'from infra_bridge import print_ts as print' para
                      sombrear o print nativo do módulo sem editar cada
                      chamada individualmente
    ED_LOG_DIR      - path dos journals do Elite
    ED_STATUS_FILE  - path do Status.json do Elite
    SCREEN_BACKEND  - string de diagnóstico do backend ativo
    KEYBOARD_BACKEND- string de diagnóstico do teclado
"""

import os
import sys
import math
import uuid
import wave
import time
import builtins as _builtins
import tempfile
import threading
import subprocess
import numpy as np
import subprocess as _subprocess


def print_ts(*args, **kwargs):
    """print com prefixo de timestamp [HH:MM:SS]. Ver nota nos exports."""
    ts = time.strftime("%H:%M:%S")
    _builtins.print(f"[{ts}]", *args, **kwargs)

# Tabela de scancodes Linux (input-event-codes.h)
# CRÍTICO: ydotool usa scancode, não o carácter ASCII.
# Sem esta tabela, 'key 1' envia Esc (scancode 1) em vez da tecla '1' (scancode 2).
_SCANCODES = {
    'esc': 1,
    '1': 2, '2': 3, '3': 4, '4': 5, '5': 6,
    '6': 7, '7': 8, '8': 9, '9': 10, '0': 11,
    'q': 16, 'w': 17, 'e': 18, 'r': 19, 't': 20,
    'y': 21, 'u': 22, 'i': 23, 'o': 24, 'p': 25,
    'a': 30, 's': 31, 'd': 32, 'f': 33, 'g': 34,
    'h': 35, 'j': 36, 'k': 37, 'l': 38,
    'z': 44, 'x': 45, 'c': 46, 'v': 47, 'b': 48,
    'n': 49, 'm': 50,
    'space': 57,
    'backspace': 14,
    'tab': 15,
    'enter': 28, 'return': 28,
    '.': 52,
    ',': 51,
    'rightshift': 54,
}

def _to_scancode(key: str) -> int:
    code = _SCANCODES.get(key.lower())
    if code is None:
        raise ValueError(f"[BRIDGE] Tecla '{key}' não mapeada em _SCANCODES. Adiciona-a à tabela.")
    return code

class _YdotoolInput:
    """
    Substituto do pydirectinput/pyautogui usando ydotool (uinput).
    Funciona em Wayland e com janelas Proton/XWayland.
    Requer: ydotoold a correr (systemd --user).
    Usa scancodes Linux explícitos para evitar a confusão
    carácter vs scancode (ex: '1' != scancode 1, que é Esc).
    """
    FAILSAFE = False

    @staticmethod
    def press(key: str):
        code = _to_scancode(key)
        _subprocess.run(["ydotool", "key", f"{code}:1"], capture_output=True)
        _subprocess.run(["ydotool", "key", f"{code}:0"], capture_output=True)

    @staticmethod
    def keyDown(key: str):
        code = _to_scancode(key)
        _subprocess.run(["ydotool", "key", f"{code}:1"], capture_output=True)

    @staticmethod
    def keyUp(key: str):
        code = _to_scancode(key)
        _subprocess.run(["ydotool", "key", f"{code}:0"], capture_output=True)

    @staticmethod
    def hotkey(*keys):
        codes = [_to_scancode(k) for k in keys]
        for c in codes:
            _subprocess.run(["ydotool", "key", f"{c}:1"], capture_output=True)
        for c in reversed(codes):
            _subprocess.run(["ydotool", "key", f"{c}:0"], capture_output=True)

    @staticmethod
    def typewrite(text: str, interval: float = 0.0):
        _subprocess.run(["ydotool", "type", "--", text], capture_output=True)

pydirectinput = _YdotoolInput()

# =====================================================================
# 2. FOCO DE JANELA — ydotool -> xdotool -> wmctrl
# =====================================================================
def _focar_janela_linux(title: str) -> bool:
    """
    Foca via gestor de janelas (xdotool/wmctrl) em vez de um clique real --
    um clique fisico aciona o que estiver associado a esse botao do rato no
    jogo (heatsink, hardpoints, etc.), independentemente de onde o cursor
    esteja no ecra. Só recorre ao clique como ultimo recurso, numa posicao
    fora da zona de jogo (100,5 -- topo do ecra, fora do cockpit 3D).
    """
    try:
        r = subprocess.run(["xdotool", "search", "--name", title, "windowactivate"],
                            timeout=3, capture_output=True, text=True)
        if r.returncode == 0:
            return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    try:
        r = subprocess.run(["wmctrl", "-a", title], timeout=3, capture_output=True, text=True)
        if r.returncode == 0:
            return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    try:
        subprocess.run(["ydotool", "mousemove", "--absolute", "100", "5"], timeout=3)
        subprocess.run(["ydotool", "click", "0xC0"], timeout=3)
        return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    print("[BRIDGE] Não foi possível focar a janela via xdotool/wmctrl/clique.")
    return False


class _LinuxWindow:
    def __init__(self, title): self.title = title
    def activate(self):
        if _focar_janela_linux(self.title):
            print(f"[BRIDGE] Janela '{self.title}' focada.")
        else:
            print(f"[BRIDGE] Foco falhou para '{self.title}'.")
    def resizeTo(self, w, h): pass
    def moveTo(self, x, y): pass

class _LinuxGetWindow:
    def getWindowsWithTitle(self, title): return [_LinuxWindow(title)]

gw = _LinuxGetWindow()

# =====================================================================
# 3. ÁUDIO — paplay -> aplay -> bell ASCII (não bloqueante)
# =====================================================================
class _LinuxWinsound:
    @staticmethod
    def Beep(frequency: int, duration_ms: int):
        def _play():
            import array as arr
            sample_rate = 44100
            n = int(sample_rate * duration_ms / 1000)
            samples = arr.array('h', [
                int(32767 * math.sin(2 * math.pi * frequency * i / sample_rate))
                for i in range(n)
            ])
            with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as tmp:
                with wave.open(tmp.name, 'w') as wf:
                    wf.setnchannels(1); wf.setsampwidth(2)
                    wf.setframerate(sample_rate); wf.writeframes(samples.tobytes())
                path = tmp.name
            for cmd in [["paplay", path], ["aplay", "-q", path]]:
                try:
                    subprocess.run(cmd, timeout=duration_ms/1000+1, capture_output=True)
                    os.unlink(path); return
                except (FileNotFoundError, subprocess.TimeoutExpired): continue
            os.unlink(path)
            print(f"\a[BRIDGE] Beep: {frequency}Hz {duration_ms}ms")
        threading.Thread(target=_play, daemon=True).start()

winsound = _LinuxWinsound()

# =====================================================================
# 4. CAPTURA DE ECRÃ — PipeWire via xdg-desktop-portal-kde
#
# ARQUITETURA SINGLETON:
#   A sessão PipeWire é aberta UMA VEZ no arranque da bridge.
#   Todas as chamadas a mss.mss() reutilizam a mesma sessão.
#   O popup KDE só aparece na primeira execução (se "Allow restoring"
#   estiver marcado, nunca mais aparece).
#
# API compatível com mss:
#   with mss.mss() as sct:
#       img = np.array(sct.grab(monitor))  # BGRA
# =====================================================================
SCREEN_WIDTH  = int(os.environ.get("R2D2_SCREEN_W", 1920))
SCREEN_HEIGHT = int(os.environ.get("R2D2_SCREEN_H", 1080))

import dbus
import dbus.mainloop.glib
import gi
gi.require_version('Gst', '1.0')
from gi.repository import GLib, Gst

Gst.init(None)
dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
# NOTA: cv2 (Qt embutido) colide com este main loop se cv2.namedWindow() for
# chamado ANTES da sessão PipeWire estar ativa ("g_main_context_push_thread_
# default: assertion 'acquired_context' failed", CreateSession nunca responde).
# Um GLib.MainContext privado por thread foi tentado e testado isoladamente,
# mas quebra a entrega dos sinais D-Bus (dbus-python não suporta bem contextos
# não-padrão aqui) — por isso mantemos o contexto padrão. A mitigação real é:
# estabelecer a sessão PipeWire ANTES de qualquer cv2.namedWindow()/imshow()
# no script chamador (ver olho.py e debug/zolho-teste.py).


class _PipeWireCapture:
    """
    Sessão de captura persistente via PipeWire + xdg-desktop-portal-kde.
    Instanciada UMA VEZ como singleton global.
    """
    def __init__(self):
        self._session_path = None
        self._pipeline     = None
        self._last_frame   = None
        self._frame_lock   = threading.Lock()
        self._ready        = threading.Event()
        self._loop         = None
        self._bus          = None
        self._loop_thread  = None
        # Token único por execução — evita colisão de session_handle_token
        # com sessões de execuções anteriores no lado do portal.
        self._run_id       = uuid.uuid4().hex[:8]

    def _iniciar(self):
        print("[CAPTURE] A iniciar sessão PipeWire screencast...")
        print("[CAPTURE] Se aparecer popup do KDE, seleciona o monitor e confirma.")
        self._bus = dbus.SessionBus()
        self._loop = GLib.MainLoop()
        self._loop_thread = threading.Thread(target=self._loop.run, daemon=True)
        self._loop_thread.start()
        # NOTA: já não confirmamos o popup automaticamente por tecla — um Enter
        # às cegas submete o diálogo antes do utilizador escolher o ecrã/janela,
        # o que causa "Pipewire stream is not ready to be streamed" no portal.
        # A escolha e confirmação têm de ser manuais (uma vez só, se não houver
        # restore_token ainda válido).
        self._criar_sessao()

        timeout_captura = 180
        if not self._ready.wait(timeout=timeout_captura):
            raise RuntimeError(f"[CAPTURE] Timeout ({timeout_captura}s). Confirma o popup de captura no KDE.")
        print("[CAPTURE] Stream PipeWire ativo.")
        # Liberta o contexto GLib padrão agora que já não há mais nenhum Response
        # D-Bus à espera — os frames chegam via callback do GStreamer (thread
        # própria do GStreamer, não depende deste main loop). Se o mantivermos a
        # correr, o Qt do cv2 nunca consegue adquirir o mesmo contexto padrão na
        # thread principal ("g_main_context_push_thread_default: assertion
        # 'acquired_context' failed") e as janelas de debug deixam de atualizar.
        self._loop.quit()
        self._loop_thread.join(timeout=3)
        # NOTA: já não clicamos automaticamente para reforçar o foco no Elite.
        # O clique físico (ydotool) cai numa posição fixa do ecrã que, consoante
        # a nave/cockpit, pode coincidir com um controlo interativo clicável
        # (ex: manete/acelerador), causando inputs de voo reais e indesejados
        # (nave a acelerar/picar sozinha). Confirmar o popup do KDE com o rato
        # tira o foco ao Elite — clica na janela do jogo manualmente se for
        # preciso antes de continuares.
        print("[CAPTURE] Se o foco saiu do Elite ao confirmar o popup, clica na janela do jogo.")
        time.sleep(1.0)

    def fechar(self):
        if self._pipeline:
            self._pipeline.set_state(Gst.State.NULL)
            self._pipeline = None
        if self._session_path and self._bus:
            try:
                obj = self._bus.get_object("org.freedesktop.portal.Desktop", self._session_path)
                dbus.Interface(obj, "org.freedesktop.portal.Session").Close()
            except Exception:
                pass
        if self._loop:
            self._loop.quit()
        if self._loop_thread:
            self._loop_thread.join(timeout=3)

    @property
    def monitors(self):
        return [
            {"left": 0, "top": 0, "width": SCREEN_WIDTH * 2, "height": SCREEN_HEIGHT},
            {"left": 0, "top": 0, "width": SCREEN_WIDTH, "height": SCREEN_HEIGHT, "is_primary": True},
        ]

    def grab(self, monitor: dict) -> np.ndarray:
        timeout = time.time() + 15
        while self._last_frame is None:
            if time.time() > timeout:
                raise RuntimeError("[CAPTURE] Timeout aguardando frame PipeWire.")
            time.sleep(0.05)
        with self._frame_lock:
            frame = self._last_frame.copy()
        x, y = monitor.get("left", 0), monitor.get("top", 0)
        w, h = monitor.get("width", SCREEN_WIDTH), monitor.get("height", SCREEN_HEIGHT)
        return frame[y:y+h, x:x+w]

    def _sender_token(self):
        return self._bus.get_unique_name().replace(".", "_").lstrip(":")

    def _criar_sessao(self):
        iface = dbus.Interface(
            self._bus.get_object("org.freedesktop.portal.Desktop",
                                 "/org/freedesktop/portal/desktop"),
            "org.freedesktop.portal.ScreenCast"
        )
        req_token = f"r2d2_req1_{self._run_id}"
        self._bus.add_signal_receiver(
            self._on_create_session,
            signal_name="Response",
            dbus_interface="org.freedesktop.portal.Request",
            path=f"/org/freedesktop/portal/desktop/request/{self._sender_token()}/{req_token}"
        )
        iface.CreateSession(dbus.Dictionary({
            "handle_token": dbus.String(req_token),
            "session_handle_token": dbus.String(f"r2d2_session_{self._run_id}"),
        }, signature="sv"))

    def _on_create_session(self, response, results):
        if response != 0:
            print(f"[CAPTURE] CreateSession falhou (response={response})")
            return
        self._session_path = str(results.get("session_handle", ""))
        self._selecionar_fontes()

    def _selecionar_fontes(self):
        iface = dbus.Interface(
            self._bus.get_object("org.freedesktop.portal.Desktop",
                                 "/org/freedesktop/portal/desktop"),
            "org.freedesktop.portal.ScreenCast"
        )
        req_token = f"r2d2_req2_{self._run_id}"
        self._bus.add_signal_receiver(
            self._on_select_sources,
            signal_name="Response",
            dbus_interface="org.freedesktop.portal.Request",
            path=f"/org/freedesktop/portal/desktop/request/{self._sender_token()}/{req_token}"
        )
        iface.SelectSources(
            dbus.ObjectPath(self._session_path),
            dbus.Dictionary({
                "handle_token": dbus.String(req_token),
                "types":        dbus.UInt32(1),
                "multiple":     dbus.Boolean(False),
                "cursor_mode":  dbus.UInt32(2),
            }, signature="sv")
        )

    def _on_select_sources(self, response, results):
        if response != 0:
            print(f"[CAPTURE] SelectSources falhou (response={response})")
            return
        self._iniciar_stream()

    def _iniciar_stream(self):
        iface = dbus.Interface(
            self._bus.get_object("org.freedesktop.portal.Desktop",
                                 "/org/freedesktop/portal/desktop"),
            "org.freedesktop.portal.ScreenCast"
        )
        req_token = f"r2d2_req3_{self._run_id}"
        self._bus.add_signal_receiver(
            self._on_start,
            signal_name="Response",
            dbus_interface="org.freedesktop.portal.Request",
            path=f"/org/freedesktop/portal/desktop/request/{self._sender_token()}/{req_token}"
        )

        # persist_mode tem de ser pedido SEMPRE (não só quando já há token) —
        # é o que faz o portal devolver um restore_token na resposta do Start.
        # Sem isto nunca se sai do ciclo "sem token -> sempre popup -> nunca gera token".
        options = {
            "handle_token": dbus.String(req_token),
            "persist_mode": dbus.UInt32(2),
        }
        token_path = os.path.join(os.path.dirname(__file__), ".pipewire_token")
        if os.path.exists(token_path):
            with open(token_path) as f:
                token = f.read().strip()
            if token:
                options["restore_token"] = dbus.String(token)
                print(f"[CAPTURE] A usar restore token: {token}")

        iface.Start(
            dbus.ObjectPath(self._session_path),
            "",
            dbus.Dictionary(options, signature="sv")
        )

    def _on_start(self, response, results):
        if response != 0:
            print(f"[CAPTURE] Start falhou (response={response})")
            return
        streams = results.get("streams", [])
        if not streams:
            print("[CAPTURE] Nenhum stream na resposta do portal.")
            return
        node_id = int(streams[0][0])
        print(f"[CAPTURE] PipeWire node ID: {node_id}")

        # Guardar restore_token para sessões futuras (elimina popup)
        restore_token = str(results.get("restore_token", ""))
        if restore_token:
            token_path = os.path.join(os.path.dirname(__file__), ".pipewire_token")
            with open(token_path, "w") as f:
                f.write(restore_token)
            print(f"[CAPTURE] Restore token guardado: {restore_token}")

        self._arrancar_gstreamer(node_id)

    def _arrancar_gstreamer(self, node_id: int):
        pipeline_str = (
            f"pipewiresrc path={node_id} ! "
            f"videoconvert ! "
            f"video/x-raw,format=BGRA ! "
            f"appsink name=sink emit-signals=true max-buffers=1 drop=true"
        )
        self._pipeline = Gst.parse_launch(pipeline_str)
        sink = self._pipeline.get_by_name("sink")
        sink.connect("new-sample", self._on_new_frame)
        self._pipeline.set_state(Gst.State.PLAYING)
        self._ready.set()

    def _on_new_frame(self, sink):
        sample = sink.emit("pull-sample")
        if sample is None:
            return Gst.FlowReturn.OK
        buf  = sample.get_buffer()
        caps = sample.get_caps().get_structure(0)
        w, h = caps.get_value("width"), caps.get_value("height")
        ok, map_info = buf.map(Gst.MapFlags.READ)
        if ok:
            frame = np.frombuffer(map_info.data, dtype=np.uint8).reshape((h, w, 4)).copy()
            with self._frame_lock:
                self._last_frame = frame
            buf.unmap(map_info)
        return Gst.FlowReturn.OK


# Singleton global: inicialização LAZY (só abre na primeira captura)
_global_session = _PipeWireCapture()
_global_session_iniciado = False

# Fecha a sessão PipeWire (pipeline + loop GLib) antes do Python finalizar as
# threads — sem isto, a thread do GLib.MainLoop ainda ativa causa um
# Segmentation Fault na finalização do interpretador (gilstate_tss_set),
# fazendo o processo terminar com exit code != 0 mesmo após sucesso.
import atexit
atexit.register(_global_session.fechar)

def _garantir_sessao():
    global _global_session_iniciado
    if not _global_session_iniciado:
        # Centra o rato ANTES do popup do KDE poder aparecer -- sem isto o
        # cursor fica onde calhar (frequentemente 0,0), o que e uma posicao
        # pouco pratica para o popup de partilha de ecra. Isto e partilhado
        # por todos os scripts, ja que todos passam por este singleton.
        try:
            subprocess.run(
                ["ydotool", "mousemove", "--absolute", str(SCREEN_WIDTH // 2), str(SCREEN_HEIGHT // 2)],
                timeout=3, capture_output=True
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
        print("[BRIDGE] A iniciar sessão de captura PipeWire (lazy)...")
        _global_session._iniciar()
        _global_session_iniciado = True

class _SingletonContext:
    """
    Context manager que expõe a sessão global sem a fechar.
    Compatível com: with mss.mss() as sct: sct.grab(monitor)
    """
    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass  # Não fecha — sessão é persistente

    @property
    def monitors(self):
        return _global_session.monitors

    def grab(self, monitor: dict) -> np.ndarray:
        _garantir_sessao()
        return _global_session.grab(monitor)


class _MssCompat:
    """Substituto do módulo mss com API idêntica."""
    @staticmethod
    def mss():
        return _SingletonContext()
    MSS = _SingletonContext


# Substitui o módulo mss em todo o processo
sys.modules['mss'] = _MssCompat()  # type: ignore
import mss  # noqa: E402 — re-importa o substituto

SCREEN_BACKEND = "pipewire_kde"
print(f"[BRIDGE] Screen backend: {SCREEN_BACKEND}")

# =====================================================================
# 5. TECLADO — requer grupo 'input'
#    Fix: sudo usermod -aG input $USER  (requer logout/login)
# =====================================================================
KEYBOARD_BACKEND = "evdev"
try:
    import evdev_keyboard as keyboard
    print("[BRIDGE] Usando evdev_keyboard para is_pressed (sem exigir root).")
except ImportError:
    print("[BRIDGE] evdev_keyboard.py não encontrado na pasta do projeto.")
    keyboard = None
    KEYBOARD_BACKEND = "unavailable"

# =====================================================================
# 6. CAMINHOS DO ELITE DANGEROUS (Steam/Proton, Nobara)
# =====================================================================
_ED_DEFAULT = (
    "/mnt/games/SteamLibrary/steamapps/compatdata/359320/pfx"
    "/drive_c/users/steamuser/Saved Games"
    "/Frontier Developments/Elite Dangerous"
)
ED_LOG_DIR     = os.environ.get("ED_LOG_DIR", _ED_DEFAULT)
ED_STATUS_FILE = os.path.join(ED_LOG_DIR, "Status.json")

if not os.path.isdir(ED_LOG_DIR):
    print(f"[BRIDGE] AVISO: ED_LOG_DIR não encontrado: {ED_LOG_DIR}\n"
          "  Override: export ED_LOG_DIR=/caminho/correto")
