"""
evdev_keyboard.py — Substituto de 'keyboard.is_pressed()' via evdev
Não exige root, respeita apenas o grupo 'input'.
"""
import evdev
from evdev import ecodes
import threading

_pressed_keys = set()
_lock = threading.Lock()
_thread_started = False


def _find_keyboard_devices():
    """Encontra todos os dispositivos de teclado físicos."""
    devices = [evdev.InputDevice(path) for path in evdev.list_devices()]
    teclados = []
    for d in devices:
        caps = d.capabilities().get(ecodes.EV_KEY, [])
        # Heurística: teclados têm muitas teclas alfanuméricas
        if ecodes.KEY_A in caps and ecodes.KEY_Z in caps:
            teclados.append(d)
    return teclados


def _listener_thread():
    global _thread_started
    dispositivos = _find_keyboard_devices()
    if not dispositivos:
        print("[EVDEV] Nenhum teclado encontrado em /dev/input.")
        return

    import selectors
    sel = selectors.DefaultSelector()
    for d in dispositivos:
        sel.register(d, selectors.EVENT_READ)

    while True:
        for key, _ in sel.select():
            device = key.fileobj
            try:
                for event in device.read():
                    if event.type == ecodes.EV_KEY:
                        keycode = ecodes.KEY.get(event.code)
                        if keycode is None:
                            continue  # Código de botão de rato ou desconhecido
                        with _lock:
                            if event.value == 1:      # down
                                _pressed_keys.add(keycode)
                            elif event.value == 0:    # up
                                _pressed_keys.discard(keycode)
            except (OSError, BlockingIOError):
                continue


def _garantir_listener():
    global _thread_started
    if not _thread_started:
        t = threading.Thread(target=_listener_thread, daemon=True)
        t.start()
        _thread_started = True


def is_pressed(tecla: str) -> bool:
    """
    Substituto de keyboard.is_pressed(tecla).
    Ex: is_pressed('q'), is_pressed('a')
    """
    _garantir_listener()
    keycode = f"KEY_{tecla.upper()}"
    with _lock:
        return keycode in _pressed_keys
